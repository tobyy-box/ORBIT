"""Media transport control.

Two strategies, deliberately separate:

``keys``
    Send the standard Windows media keys (play/pause, next, previous, stop,
    volume).  This works with any player that registers the system media keys,
    which includes Spotify, VLC, Windows Media Player, browsers and most
    desktop players.  No player-specific API is assumed.

``smtc``
    Use the Windows System Media Transport Controls session manager
    (``winsdk``) when it is installed.  This is optional because it needs an
    extra package; ORBIT reports a clear message when it is missing instead of
    pretending the control worked.

``command``
    Run a player-specific command line configured by the user, for players
    that expose a CLI (for example ``vlc --play-pause``).

Nothing here requires network access.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
from dataclasses import dataclass

from core.launcher import CREATE_NO_WINDOW, LaunchError, launch_program

WINDOWS = sys.platform == "win32"

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

TRANSPORT_KEYS = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "stop": VK_MEDIA_STOP,
    "volume_up": VK_VOLUME_UP,
    "volume_down": VK_VOLUME_DOWN,
    "mute": VK_VOLUME_MUTE,
}

TRANSPORT_LABELS = {
    "play_pause": "Play / Pause",
    "next": "Next track",
    "previous": "Previous track",
    "stop": "Stop",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "mute": "Mute",
}


@dataclass(frozen=True)
class TransportResult:
    ok: bool
    message: str
    detail: str = ""


if WINDOWS:  # pragma: no cover - Windows only
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_ushort),
            ("wParamH", ctypes.c_ushort),
        ]

    class _INPUTUNION(ctypes.Union):
        # The union must contain every member so sizeof(INPUT) matches what
        # SendInput expects (MOUSEINPUT is the largest member).
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _send_key(vk: int) -> None:
    """Press and release a virtual key through SendInput."""
    if not WINDOWS:  # pragma: no cover - non Windows fallback
        raise LaunchError("Media keys are only supported on Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    events = (_INPUT * 2)()
    for index, flags in enumerate((0, KEYEVENTF_KEYUP)):
        events[index].type = INPUT_KEYBOARD
        events[index].u.ki = _KEYBDINPUT(vk, 0, flags, 0, 0)
    sent = user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(_INPUT))
    if sent != 2:
        raise LaunchError(
            f"Windows refused the media key (error {ctypes.get_last_error()})."
        )


def _smtc_available() -> bool:
    try:
        import winsdk.windows.media.control  # noqa: F401
    except Exception:
        return False
    return True


def _run_smtc(command: str) -> TransportResult:
    """Control the active session through Windows SMTC (optional winsdk)."""
    import asyncio

    from winsdk.windows.media.control import (  # type: ignore[import-not-found]
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    async def _go() -> str:
        manager = await Manager.request_async()
        session = manager.get_current_session()
        if session is None:
            raise LaunchError("No media player is currently reporting a session.")
        if command == "play_pause":
            status = session.get_playback_info().playback_status
            # 4 == PLAYING
            if int(status) == 4:
                await session.try_pause_async()
                return "Paused"
            await session.try_play_async()
            return "Playing"
        if command == "next":
            await session.try_skip_next_async()
            return "Skipped to next track"
        if command == "previous":
            await session.try_skip_previous_async()
            return "Skipped to previous track"
        if command == "stop":
            await session.try_stop_async()
            return "Stopped"
        raise LaunchError(f"'{command}' is not supported through the media session API.")

    try:
        message = asyncio.run(_go())
    except LaunchError:
        raise
    except Exception as exc:  # winsdk raises WinRT errors
        raise LaunchError(f"The media session could not be controlled:\n{exc}") from exc
    return TransportResult(True, message)


class MediaController:
    """Configurable media control that never assumes one player's API."""

    def __init__(self, default_method: str = "keys") -> None:
        self.default_method = default_method

    def control(
        self,
        command: str,
        *,
        method: str | None = None,
        command_line: str = "",
        args: list[str] | None = None,
        working_dir: str = "",
    ) -> TransportResult:
        chosen = (method or self.default_method or "keys").strip().lower()
        if command not in TRANSPORT_KEYS:
            return TransportResult(False, f"Unknown media command: {command}")

        if chosen == "command":
            if not command_line.strip():
                return TransportResult(
                    False,
                    "This media action has no player command configured.",
                    "Set the player executable in ORBIT Studio, or switch the method to Keys.",
                )
            try:
                launch_program(command_line, args or [], working_dir=working_dir)
            except LaunchError as exc:
                return TransportResult(False, "The player command failed.", str(exc))
            return TransportResult(True, f"Sent to player: {TRANSPORT_LABELS[command]}")

        if chosen == "smtc":
            if not _smtc_available():
                return TransportResult(
                    False,
                    "Windows media sessions need the optional 'winsdk' package.",
                    "Install it with:  pip install winsdk\n"
                    "Or switch this action's method to Keys, which works with any player "
                    "that handles the system media keys.",
                )
            try:
                return _run_smtc(command)
            except LaunchError as exc:
                # Fall back to keys so the button still does something useful.
                try:
                    _send_key(TRANSPORT_KEYS[command])
                except LaunchError:
                    return TransportResult(False, "Media control failed.", str(exc))
                return TransportResult(True, f"Sent {TRANSPORT_LABELS[command]} key")

        try:
            _send_key(TRANSPORT_KEYS[command])
        except LaunchError as exc:
            return TransportResult(False, "Media key could not be sent.", str(exc))
        return TransportResult(True, f"Sent {TRANSPORT_LABELS[command]} key")


def send_keystroke_sequence(vk_codes: list[int], *, delay_ms: int = 18) -> None:
    """Send a chord/sequence of virtual keys (used by keystroke actions)."""
    if not WINDOWS:  # pragma: no cover
        raise LaunchError("Keystroke actions are only supported on Windows.")
    if not vk_codes:
        raise LaunchError("The keystroke sequence is empty.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    events = (_INPUT * (len(vk_codes) * 2))()
    # Press in order, release in reverse order so chords behave like real keys.
    for index, vk in enumerate(vk_codes):
        events[index].type = INPUT_KEYBOARD
        events[index].u.ki = _KEYBDINPUT(vk, 0, 0, 0, 0)
    for index, vk in enumerate(reversed(vk_codes)):
        slot = len(vk_codes) + index
        events[slot].type = INPUT_KEYBOARD
        events[slot].u.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)
    sent = user32.SendInput(len(events), ctypes.byref(events), ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise LaunchError("Windows blocked the keystroke (a secure window may have focus).")


def run_player_command(command: str, args: list[str] | None = None, working_dir: str = "") -> None:
    """Run a configured player command without a console window."""
    subprocess.run(
        [command, *(args or [])],
        cwd=working_dir or None,
        creationflags=CREATE_NO_WINDOW if WINDOWS else 0,
        check=False,
        timeout=15,
    )


class MediaKeyListener:
    """Reserved for future passive media observation (not used by default)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
