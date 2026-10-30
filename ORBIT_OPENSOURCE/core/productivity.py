"""Local persistence, fuzzy matching and background music-library indexing."""
from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus"}


def fuzzy_score(query: str, text: str) -> float:
    """Return a case-insensitive match score; zero means no useful match."""
    needle, haystack = query.casefold().strip(), text.casefold()
    if not needle:
        return 1.0
    if needle in haystack:
        return 100.0 - min(40.0, haystack.index(needle) * 0.2 + (len(haystack) - len(needle)) * 0.08)
    cursor, gaps, first = 0, 0, -1
    for char in needle:
        found = haystack.find(char, cursor)
        if found < 0:
            return 0.0
        if first < 0:
            first = found
        gaps += found - cursor
        cursor = found + 1
    return max(1.0, 45.0 - gaps * 2.0 - first * 0.25 - (len(haystack) - len(needle)) * 0.05)


def audio_metadata(path: str) -> dict[str, Any]:
    """Read common text metadata, duration and embedded album art."""
    from mutagen import File as MutagenFile
    audio = MutagenFile(path)
    if audio is None:
        raise ValueError("This audio format is not recognized.")
    tags = audio.tags or {}

    def tag(*names: str) -> str:
        for name in names:
            value = tags.get(name)
            if value:
                candidate = value[0] if isinstance(value, (list, tuple)) else value
                if hasattr(candidate, "text"):
                    candidate = candidate.text[0] if candidate.text else ""
                result = str(candidate).strip()
                if result:
                    return result
        return ""

    title = tag("title", "TIT2") or Path(path).stem
    artist = tag("artist", "TPE1", "albumartist", "TPE2")
    album = tag("album", "TALB")
    art = None
    pictures = getattr(audio, "pictures", []) or []
    if pictures:
        art = bytes(pictures[0].data)
    if art is None and hasattr(tags, "getall"):
        frames = tags.getall("APIC")
        if frames:
            art = bytes(frames[0].data)
    if art is None:
        encoded = tags.get("metadata_block_picture")
        if encoded:
            from mutagen.flac import Picture
            try:
                art = bytes(Picture(base64.b64decode(encoded[0])).data)
            except Exception:
                art = None
        if art is None and tags.get("coverart"):
            try:
                art = base64.b64decode(tags["coverart"][0])
            except Exception:
                art = None
    return {"path": str(Path(path)), "title": title, "artist": artist, "album": album,
            "duration": float(getattr(audio.info, "length", 0) or 0), "art": art}


class MusicLibrary(QObject):
    """Threaded incremental directory scanner; a metadata cache survives restart."""
    scanFinished = Signal(object, int)

    def __init__(self, folders: Iterable[str] = (), parent: QObject | None = None, *, cache: dict[str, dict[str, Any]] | None = None) -> None:
        super().__init__(parent)
        self.folders = list(folders)
        self.excluded: list[str] = []
        self.tracks: dict[str, dict[str, Any]] = {str(k): dict(v) for k, v in (cache or {}).items()}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._rescan_requested = False
        self._scan_complete = False

    def scan(self) -> bool:
        with self._lock:
            if self._worker and self._worker.is_alive():
                self._rescan_requested = True
                return False
            self._rescan_requested = False
            self._worker = threading.Thread(target=self._scan_worker, name="orbit-music-scan", daemon=True)
            self._worker.start()
            return True

    def _scan_worker(self) -> None:
        while True:
            self._scan_once()
            with self._lock:
                if not self._rescan_requested:
                    self._scan_complete = True
                    self._worker = None
                    return
                self._rescan_requested = False

    def _scan_once(self) -> None:
        with self._lock:
            folders = list(self.folders)
            excluded_values = list(self.excluded)
            existing = dict(self.tracks)
        paths: list[str] = []
        excluded = {os.path.normcase(os.path.abspath(value)) for value in excluded_values if value}
        for folder in folders:
            root = Path(os.path.expandvars(folder)).expanduser()
            if not root.is_dir():
                continue
            for current, dirs, files in os.walk(root, onerror=lambda _error: None):
                dirs[:] = [name for name in dirs if not name.startswith(".") and not any(
                    os.path.normcase(os.path.abspath(os.path.join(current, name))) == excluded_path
                    or os.path.normcase(os.path.abspath(os.path.join(current, name))).startswith(excluded_path + os.sep)
                    for excluded_path in excluded
                )]
                paths.extend(str(Path(current) / name) for name in files if Path(name).suffix.lower() in AUDIO_EXTENSIONS)
        # Retain cached tracks for temporarily missing folders/files; remove a stale
        # entry only once its containing configured folder was successfully scanned.
        scanned_roots = [Path(os.path.expandvars(folder)).expanduser() for folder in folders if Path(os.path.expandvars(folder)).expanduser().is_dir()]
        def is_under_scanned_root(path: str) -> bool:
            try:
                target = Path(path).resolve()
                return any(target.is_relative_to(root.resolve()) for root in scanned_roots)
            except (OSError, ValueError):
                return False
        indexed = {path: dict(value) for path, value in existing.items() if not is_under_scanned_root(path)}
        missing: list[str] = []
        for path in paths:
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                continue
            cached = existing.get(path)
            if cached is not None and cached.get("mtime") == mtime:
                indexed[path] = cached
            else:
                missing.append(path)
        with ThreadPoolExecutor(max_workers=min(4, max(1, os.cpu_count() or 1))) as pool:
            futures = {pool.submit(audio_metadata, path): path for path in missing}
            for future, path in futures.items():
                try:
                    data = future.result()
                    data["mtime"] = Path(path).stat().st_mtime
                    indexed[path] = data
                except Exception:
                    if path not in indexed:
                        indexed[path] = {"path": path, "title": Path(path).stem, "artist": "", "album": "", "duration": 0.0, "art": None, "error": "Could not read track metadata."}
        with self._lock:
            self.tracks = indexed
            snapshot = [dict(track) for track in indexed.values()]
        self.scanFinished.emit(snapshot, len(paths))

    def all_tracks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(track) for track in self.tracks.values()]

    def search(self, query: str) -> list[dict[str, Any]]:
        return sorted(self.all_tracks(), key=lambda track: fuzzy_score(query, " ".join(str(track.get(k, "")) for k in ("title", "artist", "album", "path"))), reverse=True)[:100]


@dataclass
class ProductivityData:
    notes: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    timers: list[dict[str, Any]] = field(default_factory=list)
    favorites: list[str] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)
    playlists: dict[str, list[str]] = field(default_factory=dict)
    music_queue: list[str] = field(default_factory=list)
    queue_position: int = 0
    music_track: str = ""
    music_position_ms: int = 0
    music_shuffle: bool = False
    music_repeat: str = "off"
    music_volume: int | None = None
    library_tracks: dict[str, dict[str, Any]] = field(default_factory=dict)


class ProductivityStore:
    """Atomic JSON persistence for notes, tasks, timers and local media state."""
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = ProductivityData()
        self.load()

    def load(self) -> ProductivityData:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = ProductivityData(
                    notes=[item for item in raw.get("notes", []) if isinstance(item, dict)],
                    tasks=[item for item in raw.get("tasks", []) if isinstance(item, dict)],
                    timers=[item for item in raw.get("timers", []) if isinstance(item, dict)],
                    favorites=[str(x) for x in raw.get("favorites", [])],
                    recent=[str(x) for x in raw.get("recent", [])],
                    playlists={str(k): [str(x) for x in v] for k, v in raw.get("playlists", {}).items() if isinstance(v, list)} if isinstance(raw.get("playlists", {}), dict) else {},
                    music_queue=[str(x) for x in raw.get("music_queue", [])] if isinstance(raw.get("music_queue", []), list) else [],
                    queue_position=max(0, int(raw.get("queue_position", 0))),
                    music_track=str(raw.get("music_track", "")),
                    music_position_ms=max(0, int(raw.get("music_position_ms", 0))),
                    music_shuffle=bool(raw.get("music_shuffle", False)),
                    music_repeat=str(raw.get("music_repeat", "off")) if raw.get("music_repeat") in {"off", "all", "one"} else "off",
                    music_volume=min(100, max(0, int(raw["music_volume"]))) if raw.get("music_volume") is not None else None,
                    library_tracks={str(k): v for k, v in raw.get("library_tracks", {}).items() if isinstance(v, dict)} if isinstance(raw.get("library_tracks", {}), dict) else {},
                )
        except (OSError, ValueError, TypeError, AttributeError):
            self.data = ProductivityData()
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(asdict(self.data), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def touch_recent(self, value: str) -> None:
        self.data.recent = [value, *[item for item in self.data.recent if item != value]][:100]
        self.save()


def new_record(**values: Any) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, **values}
