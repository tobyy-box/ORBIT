"""Launch-on-login behaviour.

The operating-system entry is faked here, so the suite can never change what
starts with Windows on the machine running it.
"""

from __future__ import annotations

import re
import sys

import pytest

import core.startup as startup


@pytest.fixture()
def entry(monkeypatch):
    store: dict[str, str] = {}

    def read() -> str:
        return store.get("command", "")

    def write(command: str | None) -> None:
        if command is None:
            store.pop("command", None)
        else:
            store["command"] = command

    monkeypatch.setattr(startup, "_read_entry", read)
    monkeypatch.setattr(startup, "_write_entry", write)
    return store


def test_launch_command_points_at_orbit_and_starts_it_quietly() -> None:
    command = startup.launch_command()
    parts = re.match(r'^"([^"]+)" "([^"]+)" --background$', command)
    assert parts, f"unexpected format: {command}"
    from pathlib import Path

    interpreter, entry = Path(parts.group(1)), Path(parts.group(2))
    assert interpreter.exists(), interpreter
    assert entry.name == "main.py" and entry.exists(), entry
    # pythonw.exe keeps the sign-in launch free of a console window.
    if not getattr(sys, "frozen", False):
        assert interpreter.name in {"pythonw.exe", Path(sys.executable).name}


def test_enable_then_disable_round_trips(entry) -> None:
    assert startup.is_enabled() is False

    ok, message = startup.set_enabled(True)
    assert ok, message
    assert startup.is_enabled() is True
    assert startup.entry_command() == startup.launch_command()
    assert startup.needs_repair() is False

    ok, message = startup.set_enabled(False)
    assert ok, message
    assert startup.is_enabled() is False
    assert startup.entry_command() == ""

    # Disabling twice must not be an error.
    ok, message = startup.set_enabled(False)
    assert ok, message


def test_a_dead_entry_needs_repair(entry, tmp_path) -> None:
    entry["command"] = str(tmp_path / "moved-away.exe")
    assert startup.needs_repair() is True

    entry["command"] = startup.launch_command()
    assert startup.needs_repair() is False

    entry.pop("command", None)
    assert startup.needs_repair() is False


def test_a_write_failure_is_reported_instead_of_raised(monkeypatch) -> None:
    def boom(_command: str | None) -> None:
        raise OSError("access is denied")

    monkeypatch.setattr(startup, "_write_entry", boom)
    ok, message = startup.set_enabled(True)
    assert ok is False
    assert "could not" in message.lower()
    assert "access is denied" in message
