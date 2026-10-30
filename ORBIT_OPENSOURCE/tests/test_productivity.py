"""Focused tests for local productivity services and feature-panel behavior."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config_manager import ConfigManager, default_config
from core.productivity import MusicLibrary, ProductivityStore, fuzzy_score
from core.models import Action
from ui.productivity import ProductivityPanel, build_search_index


def test_fuzzy_search_handles_substrings_and_small_mistypes() -> None:
    assert fuzzy_score("calc", "Windows Calculator") > 0
    assert fuzzy_score("wrd", "Word") > 0
    assert fuzzy_score("xyz", "Calculator") == 0


def test_search_index_walks_recursively_deduplicates_and_honors_exclusions(tmp_path: Path) -> None:
    included = tmp_path / "included"
    nested = included / "nested"
    excluded = nested / "private"
    excluded.mkdir(parents=True)
    (nested / "notes.txt").write_text("hello", encoding="utf-8")
    (nested / "tool.exe").write_text("", encoding="utf-8")
    (excluded / "secret.txt").write_text("", encoding="utf-8")
    config = default_config()
    config.settings.search_index_apps = False
    config.settings.search_folders = [str(included)]
    config.settings.search_excluded = [str(excluded)]
    entries = build_search_index(config)
    paths = {os.path.normcase(os.path.abspath(item["path"])) for item in entries if item.get("path")}
    assert os.path.normcase(os.path.abspath(nested / "notes.txt")) in paths
    assert os.path.normcase(os.path.abspath(excluded / "secret.txt")) not in paths
    indexed_paths = [os.path.normcase(os.path.abspath(item["path"])) for item in entries if item.get("type") in {"Application", "Shortcut", "Folder", "File"}]
    assert len(indexed_paths) == len(set(indexed_paths))


def test_search_index_includes_actions_and_enabled_widgets() -> None:
    config = default_config()
    config.settings.search_index_apps = False
    config.settings.widgets_enabled = ["timer", "tasks"]
    config.root_menu().actions.append(Action(id="search-action", name="Deploy", type="command", target="deploy.exe"))
    entries = build_search_index(config)
    assert any(item.get("favorite_key") == "search-action" for item in entries)
    assert {item.get("widget_tab") for item in entries if item["type"] == "ORBIT Widget"} == {"timer", "tasks"}


def test_productivity_store_round_trips_feature_data(tmp_path: Path) -> None:
    store = ProductivityStore(tmp_path / "productivity.json")
    store.data.music_queue = ["C:/music/song.mp3"]
    store.data.music_volume = 43
    store.data.playlists = {"Favorites": ["C:/music/song.mp3"]}
    store.data.notes = [{"id": "note1", "title": "Idea", "text": "one\ntwo", "pinned": True}]
    store.data.tasks = [{"id": "task1", "title": "Ship", "done": False}]
    store.save()
    restored = ProductivityStore(store.path).data
    assert restored.music_queue == store.data.music_queue
    assert restored.music_volume == 43
    assert restored.playlists == store.data.playlists
    assert restored.notes == store.data.notes
    assert restored.tasks == store.data.tasks


def test_music_library_retains_cached_tracks_when_folder_is_offline(qapp, tmp_path: Path) -> None:
    offline = tmp_path / "offline"
    cached_path = offline / "saved.mp3"
    library = MusicLibrary([str(offline)], qapp, cache={str(cached_path): {"path": str(cached_path), "title": "Saved", "mtime": 1}})
    done: list[tuple[list[dict], int]] = []
    library.scanFinished.connect(lambda tracks, count: done.append((tracks, count)))
    assert library.scan() is True
    deadline = time.monotonic() + 3
    while not done and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    assert done
    assert done[0][1] == 0
    assert any(track["title"] == "Saved" for track in done[0][0])


def test_productivity_panel_notes_tasks_and_timers_persist(qapp, tmp_path: Path) -> None:
    data = ProductivityStore(tmp_path / "widgets.json")
    config = default_config()
    config.settings.search_index_apps = False
    manager = ConfigManager(tmp_path / "config.json")
    manager.config = config
    controller = SimpleNamespace(
        config_manager=manager,
        tray=SimpleNamespace(notify=lambda *args, **kwargs: None),
        theme=SimpleNamespace(accent="#6C8CFF"),
    )
    panel = ProductivityPanel(controller, data)
    try:
        panel._new_note()
        panel.note_title.setText("Quick idea")
        panel.note_text.setPlainText("Remember this")
        assert data.data.notes[0]["title"] == "Quick idea"
        assert data.data.notes[0]["text"] == "Remember this"
        panel.task_edit.setText("Finish tests")
        panel._add_task()
        assert data.data.tasks[0]["title"] == "Finish tests"
        panel.minutes.setValue(0)
        panel.seconds.setValue(3)
        panel._start_timer()
        assert data.data.timers[-1]["running"] is True
        panel.hide()
        data.data.timers[-1]["deadline"] = time.time() - 1
        panel._tick()
        assert data.data.timers[-1]["running"] is False
        assert data.data.timers[-1]["remaining"] == 0
        panel.apply_settings(config.settings)
        assert panel.tabs.count() == 4
    finally:
        panel.shutdown()
        panel.deleteLater()


def test_music_preferences_write_to_main_config_without_interrupting_player(qapp, tmp_path: Path) -> None:
    config = default_config()
    manager = ConfigManager(tmp_path / "config.json")
    manager.config = config
    store = ProductivityStore(tmp_path / "music.json")
    from ui.productivity import MusicPlayer

    player = MusicPlayer(config.settings, store)
    controller = SimpleNamespace(config_manager=manager, apply_settings=lambda **kwargs: None)
    player.controller = controller
    try:
        player.settings.music_playlists = {"Focus": ["C:/music/focus.flac"]}
        player._persist_music_preferences()
        assert manager.config.settings.music_playlists == player.settings.music_playlists
        assert manager.config.settings.music_queue == player._queue
        assert manager.config.settings.music_volume == player.volume.value()
    finally:
        player.shutdown()
        player.deleteLater()
