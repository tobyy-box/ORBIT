"""Operating-system launching helpers.

Design rules:

* User text is never turned into a shell command line unless the action
  explicitly opts into ``shell`` mode.
* Paths (including ones with spaces or non-English characters) are passed to
  the OS as single arguments, never string-concatenated.
* Failures raise :class:`LaunchError` with an explanation a person can read.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

if WINDOWS:  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CREATE_NEW_CONSOLE = 0x00000010
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    _SW_RESTORE = 9
    _SW_SHOW = 5
    _ASFW_ANY = -1
else:
    CREATE_NEW_CONSOLE = CREATE_NO_WINDOW = DETACHED_PROCESS = 0
    CREATE_NEW_PROCESS_GROUP = 0


class LaunchError(RuntimeError):
    """A user-facing failure while launching or controlling something."""


# --------------------------------------------------------------------- opening
def open_with_default(path: str) -> None:
    """Open a file or folder with the operating system's default handler."""
    target = Path(path).expanduser()
    if not target.exists():
        raise LaunchError(f"Path does not exist:\n{target}")
    try:
        if WINDOWS:
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif MACOS:
            subprocess.Popen(["open", str(target)], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(target)], close_fds=True)
    except OSError as exc:
        raise LaunchError(f"Windows could not open this item:\n{exc}") from exc


def open_url(url: str) -> None:
    """Open ``url`` in the default browser."""
    from core.urls import validate_url

    problem = validate_url(url)
    if problem:
        raise LaunchError(problem)
    try:
        if WINDOWS:
            os.startfile(url)  # type: ignore[attr-defined]
        elif MACOS:
            subprocess.Popen(["open", url], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", url], close_fds=True)
    except OSError as exc:
        raise LaunchError(f"The browser could not be started:\n{exc}") from exc


# -------------------------------------------------------------------- programs
def resolve_executable(program: str) -> str | None:
    """Return an executable path for ``program`` if one exists on this machine."""
    if not program:
        return None
    candidate = Path(os.path.expandvars(os.path.expanduser(program.strip('"'))))
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(program)
    return found


def _launch_kwargs(*, capture: bool, hidden: bool, console: bool = False) -> dict[str, object]:
    """Process creation flags that keep the menu responsive."""
    kwargs: dict[str, object] = {
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
    }
    if not WINDOWS:
        return kwargs
    if capture or hidden:
        kwargs["creationflags"] = CREATE_NO_WINDOW
        if capture:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        else:
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
    elif console:
        # Explicitly requested console window (for example an interactive CLI).
        kwargs["creationflags"] = CREATE_NEW_CONSOLE
    else:
        # Detached: the launched program must not die with ORBIT and never
        # shows a stray console window.
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return kwargs


def launch_program(
    program: str,
    args: Sequence[str] = (),
    *,
    working_dir: str = "",
    shell: bool = False,
    capture: bool = False,
    hidden: bool = False,
    console: bool = False,
    wait: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.Popen[str]:
    """Start a program directly and return the process.

    ``shell=True`` runs the configured command through ``cmd.exe /c`` and is
    only used when the action author explicitly enables it.
    """
    program = (program or "").strip()
    if not program:
        raise LaunchError("No program is configured for this action.")

    argv: list[str]
    kwargs = _launch_kwargs(capture=capture, hidden=hidden, console=console)
    text_mode = {"text": True, "encoding": "utf-8", "errors": "replace"}

    if shell and WINDOWS:
        command = program
        if args:
            command = " ".join([program, *[f'"{arg}"' if " " in arg else arg for arg in args]])
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        kwargs["creationflags"] = CREATE_NO_WINDOW if capture or hidden else 0
    else:
        resolved = resolve_executable(program)
        if resolved is None:
            raise LaunchError(
                f"Program not found:\n{program}\n\n"
                "Use the Browse button in ORBIT Studio to pick the executable, "
                "or add it to your PATH."
            )
        argv = [resolved, *[str(arg) for arg in args]]

    cwd = working_dir.strip() or None
    if cwd and not Path(cwd).is_dir():
        raise LaunchError(f"Working folder does not exist:\n{cwd}")

    try:
        if wait:
            return subprocess.run(
                argv,
                cwd=cwd,
                timeout=timeout,
                check=False,
                **kwargs,  # type: ignore[arg-type]
                **text_mode,
            )
        if capture:
            # The caller reads the pipes itself (interactive, cancellable).
            kwargs.update(text_mode)  # type: ignore[arg-type]
        return subprocess.Popen(  # type: ignore[call-overload]
            argv,
            cwd=cwd,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise LaunchError(f"Program not found:\n{exc.filename or program}") from exc
    except OSError as exc:
        raise LaunchError(f"Windows could not start this program:\n{exc}") from exc


# --------------------------------------------------------------- introspection
def is_process_running(executable_name: str) -> bool:
    """Best-effort check used by visibility conditions.

    Uses a process list rather than shelling out so it works without a console.
    """
    name = os.path.basename(executable_name.strip().strip('"')).lower()
    if not name:
        return False
    if WINDOWS:
        try:
            output = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=CREATE_NO_WINDOW,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return name in output.lower()
    try:
        output = subprocess.run(
            ["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=4
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return name in output.lower()


# ------------------------------------------------------------ window handling
def set_foreground_hwnd(hwnd: int) -> bool:
    """Force ``hwnd`` to the foreground so the overlay reliably receives keys.

    Windows only lets the foreground process change the foreground window; if
    the call is refused ORBIT still shows the overlay and falls back to its
    own focus handling, so this is never allowed to be a hard requirement.
    """
    if not WINDOWS or not hwnd:
        return False
    try:  # pragma: no cover - Windows only
        _user32.AllowSetForegroundWindow(_ASFW_ANY)
        _user32.ShowWindow(wintypes.HWND(hwnd), _SW_RESTORE)
        result = _user32.SetForegroundWindow(wintypes.HWND(hwnd))
        return bool(result)
    except Exception:
        return False


def flash_window(hwnd: int) -> None:
    if not WINDOWS or not hwnd:
        return
    try:  # pragma: no cover - Windows only
        _user32.FlashWindow(wintypes.HWND(hwnd), True)
    except Exception:
        pass


def show_console_window() -> bool:
    """True when a console window is attached to this process (dev runs)."""
    if not WINDOWS:
        return sys.stdout is not None and sys.stdout.isatty()
    try:  # pragma: no cover - Windows only
        return bool(_kernel32.GetConsoleWindow())
    except Exception:
        return False
