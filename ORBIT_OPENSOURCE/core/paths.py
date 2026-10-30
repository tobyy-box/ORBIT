"""Filesystem locations and path helpers used across ORBIT."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ORBIT"


def app_data_dir() -> Path:
    """Return ORBIT's per-user data directory, creating it if needed."""
    override = os.environ.get("ORBIT_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    """Path of the live ``config.json``."""
    return app_data_dir() / "config.json"


def backups_dir() -> Path:
    """Directory holding timestamped configuration backups."""
    path = app_data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Directory holding the rotating log file."""
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return logs_dir() / "orbit.log"


def project_root() -> Path:
    """Directory that contains ``main.py`` (or the frozen executable)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return project_root() / "assets"


def expand_path(value: str | os.PathLike[str] | None) -> str:
    """Expand ``%VARS%``, ``~`` and normalise separators.

    Windows paths containing spaces or non-English characters are preserved
    untouched apart from expansion; ORBIT always passes them to the OS as
    single arguments rather than building a command line string.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        text = text[1:-1]
    expanded = os.path.expandvars(text)
    expanded = os.path.expanduser(expanded)
    return expanded


def home_dir() -> str:
    return str(Path.home())
