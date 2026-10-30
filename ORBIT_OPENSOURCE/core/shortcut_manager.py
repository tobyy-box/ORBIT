"""Global activation shortcut.

Windows is handled through ``RegisterHotKey`` on a dedicated thread with its own
message loop.  That gives ORBIT three properties the usual Python keyboard
libraries cannot offer without extra dependencies or administrator rights:

* it works while another application has focus;
* registration fails loudly if the combination is already taken by another
  program (``ERROR_HOTKEY_ALREADY_REGISTERED``), so ORBIT can tell the user
  instead of silently doing nothing;
* no low-level keyboard hook is installed, so ORBIT never sees your keystrokes.

On other platforms ORBIT falls back to ``pynput`` when it is installed and
reports clearly when it is not.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from core.keycodes import KeyNotationError, MOD_NOREPEAT, format_hotkey, parse_hotkey

WINDOWS = sys.platform == "win32"

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0x4F52  # 'OR'

if WINDOWS:  # pragma: no cover - Windows only
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hWnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]


@dataclass(slots=True)
class ShortcutState:
    """Result of the last registration attempt."""

    ok: bool
    hotkey: str = ""
    display: str = ""
    message: str = ""
    conflict: bool = False


def _win_error_text(code: int) -> str:
    try:
        return ctypes.FormatError(code).strip()
    except Exception:
        return f"Windows error {code}"


class ShortcutManager(QObject):
    """Registers one global shortcut and reports activation."""

    activated = Signal()
    state_changed = Signal(object)  # ShortcutState
    message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._registered = False
        self._hotkey = ""
        self.state = ShortcutState(False, message="Not started.")
        self._pynput_listener = None

    # -------------------------------------------------------------------- public
    @property
    def hotkey(self) -> str:
        return self._hotkey

    @property
    def is_registered(self) -> bool:
        return self._registered

    def start(self, hotkey: str) -> ShortcutState:
        """Register ``hotkey`` as the global activation shortcut."""
        self.stop()
        canonical = hotkey.strip()
        try:
            modifiers, key = parse_hotkey(canonical)
        except (KeyNotationError, AttributeError) as exc:
            state = ShortcutState(False, canonical, message=str(exc))
            self._set_state(state)
            return state

        if WINDOWS:
            state = self._register_windows(modifiers, key, canonical)
        else:
            state = self._register_portable(canonical)
        self._set_state(state)
        return state

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            thread_id = self._thread_id
        if thread is not None and thread.is_alive():
            if WINDOWS and thread_id:
                try:  # pragma: no cover - Windows only
                    ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
                except Exception:
                    pass
            thread.join(timeout=1.5)
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        self._registered = False
        self._thread = None
        self._thread_id = None

    def verify_supported(self) -> str:
        """Human readable note about how activation is wired on this OS."""
        if WINDOWS:
            return "Registered through the Windows hotkey API. No keyboard hook is installed."
        if self._pynput_available():
            return "Using pynput for the global shortcut on this platform."
        return (
            "Global shortcuts need the optional 'pynput' package on this platform.\n"
            "Install it with:  pip install pynput\n"
            "Until then, open the menu from the tray icon."
        )

    # -------------------------------------------------------------------- internals
    def _set_state(self, state: ShortcutState) -> None:
        self.state = state
        if state.message:
            self.message.emit(state.message)
        self.state_changed.emit(state)

    def _register_windows(self, modifiers: int, key: int, canonical: str) -> ShortcutState:  # pragma: no cover
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND if WINDOWS else ctypes.c_void_p,
            ctypes.c_int,
            wintypes.UINT if WINDOWS else ctypes.c_uint,
            wintypes.UINT if WINDOWS else ctypes.c_uint,
        ]
        user32.RegisterHotKey.restype = wintypes.BOOL

        with self._lock:
            self._stop_event = threading.Event()

        self._thread = threading.Thread(
            target=self._win_message_loop,
            args=(modifiers, key, canonical),
            name="orbit-hotkey",
            daemon=True,
        )
        self._thread.start()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._registered or self.state.conflict:
                break
            time.sleep(0.02)
        if self._registered:
            return ShortcutState(
                True,
                canonical,
                format_hotkey(canonical),
                f"Active: {format_hotkey(canonical)}",
            )
        if self.state.conflict:
            return self.state
        return ShortcutState(
            False,
            canonical,
            format_hotkey(canonical),
            "The shortcut could not be registered. Check that no other ORBIT window is "
            "already running, then try again.",
        )

    def _win_message_loop(self, modifiers: int, key: int, canonical: str) -> None:  # pragma: no cover - Windows only
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = kernel32.GetCurrentThreadId()
        flags = modifiers | MOD_NOREPEAT
        if not user32.RegisterHotKey(None, HOTKEY_ID, flags, key):
            error = ctypes.get_last_error()
            if error == ERROR_HOTKEY_ALREADY_REGISTERED:
                self._set_state(
                    ShortcutState(
                        False,
                        canonical,
                        format_hotkey(canonical),
                        f"{format_hotkey(canonical)} is already used by another application.\n"
                        "Choose a different activation shortcut in ORBIT Studio → Behaviour.",
                        conflict=True,
                    )
                )
            else:
                self._set_state(
                    ShortcutState(
                        False,
                        canonical,
                        format_hotkey(canonical),
                        f"Windows refused {format_hotkey(canonical)}: {_win_error_text(error)}",
                    )
                )
            return

        self._registered = True
        self._hotkey = canonical
        self.state_changed.emit(
            ShortcutState(True, canonical, format_hotkey(canonical), f"Active: {format_hotkey(canonical)}")
        )
        message = MSG()
        try:
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:  # WM_QUIT
                    break
                if result == -1:  # error
                    break
                if message.message == WM_HOTKEY:
                    self.activated.emit()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False
            self._hotkey = ""

    # ---------------------------------------------------------------- portable
    @staticmethod
    def _pynput_available() -> bool:
        try:
            import pynput.keyboard  # noqa: F401
        except Exception:
            return False
        return True

    def _register_portable(self, canonical: str) -> ShortcutState:  # pragma: no cover - non Windows
        if not self._pynput_available():
            return ShortcutState(
                False,
                canonical,
                format_hotkey(canonical),
                "Global shortcuts on this platform need the optional 'pynput' package.\n"
                "Install it with:  pip install pynput\n"
                "You can still open ORBIT from the tray icon.",
            )
        from pynput import keyboard as pynput_keyboard

        try:
            listener = pynput_keyboard.GlobalHotKeys({f"<{canonical.replace('+', '>+<')}>": self.activated.emit})
        except Exception as exc:  # invalid combination for pynput
            return ShortcutState(False, canonical, format_hotkey(canonical), f"That shortcut is not supported here:\n{exc}")
        listener.daemon = True
        listener.start()
        self._pynput_listener = listener
        self._registered = True
        self._hotkey = canonical
        return ShortcutState(True, canonical, format_hotkey(canonical), f"Active: {format_hotkey(canonical)}")
