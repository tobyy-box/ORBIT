"""Keyboard notation parsing shared by the hotkey manager and keystroke actions.

Notation is a ``+`` separated list, for example ``alt+space``, ``ctrl+shift+s``
or ``win+shift+left``.  Modifiers may be written ``ctrl``/``control``,
``alt``, ``shift``, ``win``/``super``/``meta``.
"""

from __future__ import annotations

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MODIFIER_ALIASES = {
    "alt": "alt",
    "option": "alt",
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "cmd": "win",
    "command": "win",
}

# Named keys -> Windows virtual key code.
NAMED_KEYS: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "super": 0x5B,
    "meta": 0x5B,
    "apps": 0x5D,
    "num0": 0x60,
    "num1": 0x61,
    "num2": 0x62,
    "num3": 0x63,
    "num4": 0x64,
    "num5": 0x65,
    "num6": 0x66,
    "num7": 0x67,
    "num8": 0x68,
    "num9": 0x69,
    "multiply": 0x6A,
    "add": 0x6B,
    "subtract": 0x6D,
    "decimal": 0x6E,
    "divide": 0x6F,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "volumeup": 0xAF,
    "volumedown": 0xAE,
    "volumemute": 0xAD,
    "mediaplaypause": 0xB3,
    "playpause": 0xB3,
    "medianext": 0xB0,
    "nexttrack": 0xB0,
    "mediaprev": 0xB1,
    "prevtrack": 0xB1,
    "mediastop": 0xB2,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
    "+": 0xBB,  # shifted '='
}

SPECIAL_KEY_NAMES = {
    ",", ".", "/", ";", "'", "[", "]", "\\", "`", "-", "=",
}

_NAMED_DISPLAY = {
    "esc": "Esc",
    "escape": "Esc",
    "enter": "Enter",
    "return": "Enter",
    "backspace": "Backspace",
    "tab": "Tab",
    "space": "Space",
    "pageup": "Page Up",
    "pagedown": "Page Down",
    "end": "End",
    "home": "Home",
    "left": "Left",
    "up": "Up",
    "right": "Right",
    "down": "Down",
    "insert": "Insert",
    "delete": "Delete",
    "del": "Delete",
    "printscreen": "Print Screen",
    "capslock": "Caps Lock",
    "numlock": "Num Lock",
    "scrolllock": "Scroll Lock",
    "apps": "Menu",
    "pause": "Pause",
    "volumeup": "Volume Up",
    "volumedown": "Volume Down",
    "volumemute": "Mute",
    "mediaplaypause": "Play/Pause",
    "playpause": "Play/Pause",
    "medianext": "Next Track",
    "nexttrack": "Next Track",
    "mediaprev": "Previous Track",
    "prevtrack": "Previous Track",
    "mediastop": "Stop",
    "multiply": "Multiply",
    "add": "Add",
    "subtract": "Subtract",
    "decimal": "Decimal",
    "divide": "Divide",
}

MODIFIER_DISPLAY = {"alt": "Alt", "ctrl": "Ctrl", "shift": "Shift", "win": "Win"}
MODIFIER_MASK = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


class KeyNotationError(ValueError):
    """Raised when a keyboard string cannot be parsed."""


def _split(text: str) -> list[str]:
    return [part.strip() for part in str(text).replace("＋", "+").split("+")]


def _token_to_vk(token: str) -> int:
    lowered = token.lower()
    if lowered in NAMED_KEYS:
        return NAMED_KEYS[lowered]
    if lowered.startswith("f") and lowered[1:].isdigit():
        number = int(lowered[1:])
        if 1 <= number <= 24:
            return 0x70 + (number - 1)
    if len(lowered) == 1:
        char = lowered
        if "a" <= char <= "z":
            return ord(char.upper())
        if "0" <= char <= "9":
            return ord(char)
    raise KeyNotationError(f"Unknown key: {token!r}")


def parse_hotkey(text: str) -> tuple[int, int]:
    """Parse ``"alt+space"`` into ``(modifier_mask, virtual_key)``."""
    tokens = [token for token in _split(text) if token]
    if not tokens:
        raise KeyNotationError("Shortcut is empty.")
    modifiers = 0
    key: int | None = None
    for token in tokens:
        lowered = token.lower()
        if lowered in MODIFIER_ALIASES:
            modifiers |= MODIFIER_MASK[MODIFIER_ALIASES[lowered]]
            continue
        if key is not None:
            raise KeyNotationError("A shortcut can contain only one non-modifier key.")
        key = _token_to_vk(token)
    if key is None:
        raise KeyNotationError("A shortcut needs a key besides the modifiers.")
    return modifiers, key


def format_hotkey(text: str) -> str:
    """Return a canonical display form such as ``Alt + Space``."""
    try:
        tokens = [token for token in _split(text) if token]
        if not tokens:
            return ""
        parts: list[str] = []
        for token in tokens:
            lowered = token.lower()
            if lowered in MODIFIER_ALIASES:
                parts.append(MODIFIER_DISPLAY[MODIFIER_ALIASES[lowered]])
                continue
            if lowered in _NAMED_DISPLAY:
                parts.append(_NAMED_DISPLAY[lowered])
            elif len(token) == 1:
                parts.append(token.upper())
            else:
                parts.append(token.capitalize())
        return " + ".join(parts)
    except KeyNotationError:
        return str(text)


def validate_hotkey(text: str, *, require_modifier: bool = False) -> str | None:
    """Return an error message, or ``None`` when ``text`` is a valid shortcut."""
    try:
        modifiers, _key = parse_hotkey(text)
    except KeyNotationError as exc:
        return str(exc)
    if require_modifier and modifiers == 0:
        return "Add at least one modifier (Ctrl, Alt, Shift or Win)."
    return None


def _split_sequence(text: str) -> list[str]:
    """Split on ``,``/``/`` separators while keeping a comma inside a chord.

    ``alt+,`` is the comma key; ``ctrl+c, enter`` is two presses.
    """
    parts: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text):
        if char in ",/":
            previous = text[index - 1] if index else ""
            following = text[index + 1] if index + 1 < len(text) else ""
            if previous == "+" or following == "+":
                current.append(char)
                continue
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def parse_keystrokes(text: str) -> list[int]:
    """Parse a keystroke *sequence*, e.g. ``"ctrl+shift+s, enter"``.

    Returns a flat list of virtual key codes in press order.  ``+`` builds a
    chord (modifiers held), ``/`` or ``,`` separates consecutive presses.
    """
    sequence: list[int] = []
    for chord in _split_sequence(str(text)):
        chord = chord.strip()
        if not chord:
            continue
        keys = [_token_to_vk(token) for token in _split(chord) if token]
        if not keys:
            continue
        sequence.extend(keys)
    if not sequence:
        raise KeyNotationError("Keystroke sequence is empty.")
    return sequence
