"""Shared pytest fixtures.

The suite never touches a real ORBIT configuration: ``ORBIT_DATA_DIR`` points at
a temporary directory before any ORBIT module is imported.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Isolate every test run from the user's real %APPDATA%\ORBIT data.
_ORBIT_DATA = tempfile.mkdtemp(prefix="orbit-tests-")
os.environ["ORBIT_DATA_DIR"] = _ORBIT_DATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.config_manager import ConfigManager, default_config  # noqa: E402
from core.models import Config  # noqa: E402


@pytest.fixture()
def data_dir() -> Path:
    return Path(_ORBIT_DATA)


@pytest.fixture()
def config() -> Config:
    return default_config()


@pytest.fixture()
def manager(tmp_path: Path) -> ConfigManager:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load()
    return manager


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen QApplication for UI tests."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
