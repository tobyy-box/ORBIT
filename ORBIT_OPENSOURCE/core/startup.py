"""Launch-on-login integration.

Windows uses the per-user ``Run`` key: no administrator rights, easy to
inspect and easy to remove.  Other platforms write a ``.desktop`` file.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

from core.paths import APP_NAME

APP_KEY = "ORBIT"
WINDOWS = sys.platform == "win32"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """The command ORBIT wants the operating system to run at sign-in."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    entry = Path(__file__).resolve().parent.parent / "main.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{entry}" --background'


def _desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "orbit.desktop"


def _read_entry() -> str:
    """The command the OS will run at sign-in, or ``""`` when there is none."""
    if WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, APP_KEY)
        except OSError:
            return ""
        return str(value or "")
    try:
        text = _desktop_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("Exec="):
            return line[5:].strip()
    return ""


def _write_entry(command: str | None) -> None:
    """Store ``command`` as the sign-in entry; ``None`` removes it."""
    if WINDOWS:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if command is None:
                try:
                    winreg.DeleteValue(key, APP_KEY)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, APP_KEY, 0, winreg.REG_SZ, command)
        return

    desktop = _desktop_path()
    if command is None:
        if desktop.exists():
            desktop.unlink()
        return
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={shlex.join([sys.executable, str(Path(__file__).resolve().parent.parent / 'main.py'), '--background'])}\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def entry_command() -> str:
    """What is currently registered for sign-in (``""`` when disabled)."""
    return _read_entry()


def is_enabled() -> bool:
    return bool(_read_entry())


def needs_repair() -> bool:
    """True when the entry exists but points at a file that no longer exists.

    This happens when the executable moves, or when ORBIT switches between the
    source tree and a packaged build: Windows would keep trying to start a path
    that is gone, and the app would silently stop starting with the PC.
    """
    command = _read_entry().strip()
    if not command:
        return False
    match = re.match(r'\s*"([^"]+)"', command) or re.match(r"\s*(\S+)", command)
    if match is None:
        return False
    try:
        return not Path(match.group(1)).exists()
    except OSError:  # pragma: no cover - malformed path
        return True


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Enable or disable auto-start. Returns ``(ok, message)``."""
    try:
        _write_entry(launch_command() if enabled else None)
    except OSError as exc:
        if WINDOWS:
            return False, f"Windows could not update the startup entry:\n{exc}"
        return False, f"The autostart entry could not be written:\n{exc}"
    return True, "Launch on login enabled." if enabled else "Launch on login disabled."


def describe_support() -> str:
    if WINDOWS:
        return "Adds ORBIT to your user startup items (HKCU\\...\\Run). No admin rights needed."
    return "Writes an autostart .desktop entry in your user config directory."
