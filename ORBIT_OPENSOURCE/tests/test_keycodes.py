"""Keyboard notation parsing shared by the hotkey manager and keystroke actions."""

from __future__ import annotations

import pytest

from core.keycodes import (
    KeyNotationError,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    format_hotkey,
    parse_hotkey,
    parse_keystrokes,
    validate_hotkey,
)


def test_parse_simple_combinations() -> None:
    assert parse_hotkey("alt+space") == (MOD_ALT, 0x20)
    assert parse_hotkey("Ctrl+Shift+S") == (MOD_CONTROL | MOD_SHIFT, ord("S"))
    assert parse_hotkey("win+f1") == (MOD_WIN, 0x70)
    assert parse_hotkey("ctrl+alt+delete") == (MOD_CONTROL | MOD_ALT, 0x2E)


def test_aliases_are_normalised() -> None:
    assert parse_hotkey("control+option+space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("super+,") == (MOD_WIN, 0xBC)


@pytest.mark.parametrize("value", ["", "   ", "ctrl+", "ctrl+alt", "ctrl+a+b", "ctrl+banana"])
def test_invalid_hotkeys_raise(value: str) -> None:
    with pytest.raises(KeyNotationError):
        parse_hotkey(value)


def test_validate_hotkey_messages() -> None:
    assert validate_hotkey("alt+space") is None
    assert validate_hotkey("") is not None
    assert validate_hotkey("space", require_modifier=True) is not None
    assert validate_hotkey("ctrl+space", require_modifier=True) is None


def test_format_hotkey_is_human_readable() -> None:
    assert format_hotkey("alt+space") == "Alt + Space"
    assert format_hotkey("ctrl+shift+s") == "Ctrl + Shift + S"
    assert format_hotkey("win+f5") == "Win + F5"
    assert format_hotkey("alt+,") == "Alt + ,"


def test_keystroke_sequences() -> None:
    assert parse_keystrokes("ctrl+c") == [0x11, ord("C")]
    assert parse_keystrokes("ctrl+shift+s, enter") == [0x11, 0x10, ord("S"), 0x0D]
    assert parse_keystrokes("win+shift+s") == [0x5B, 0x10, ord("S")]
    # A comma inside a chord is the comma key, not a separator.
    assert parse_keystrokes("alt+,") == [0x12, 0xBC]
    assert parse_keystrokes("alt+,, enter") == [0x12, 0xBC, 0x0D]
    assert parse_keystrokes("ctrl+a/ctrl+v") == [0x11, ord("A"), 0x11, ord("V")]


def test_empty_keystrokes_raise() -> None:
    with pytest.raises(KeyNotationError):
        parse_keystrokes("   ")
