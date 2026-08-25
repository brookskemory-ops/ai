"""The games list: load, mutate, save.

Storage is a single JSON file. It is written atomically (temp file + replace)
so a crash mid-save can never leave you with a truncated library.
"""

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# Fields a client is allowed to set directly. Playtime and counters are owned
# by the server so the browser cannot invent 400 hours of Solitaire.
EDITABLE_FIELDS = ("name", "exe", "args", "cwd", "favorite", "notes", "cover")


def _now() -> float:
    return time.time()


def new_game(name: str, exe: str, **extra: Any) -> Dict[str, Any]:
    game = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "exe": exe,
        "args": "",
        "cwd": None,          # None means "folder the exe lives in"
        "cover": None,        # filename inside the covers dir
        "favorite": False,
        "notes": "",
        "added": _now(),
        "last_played": None,
        "play_seconds": 0,
        "play_count": 0,
    }
    for key, value in extra.items():
        if key in EDITABLE_FIELDS:
            game[key] = value
    return game


class Library:
    """A JSON-backed collection of games, safe to share across threads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._games: List[Dict[str, Any]] = []
        self.load()

    # -- persistence ----------------------------------------------------

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._games = []
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # A corrupt library is kept aside rather than silently dropped.
                backup = self.path.with_suffix(".corrupt.json")
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                self._games = []
                return

            games = raw.get("games", []) if isinstance(raw, dict) else []
            merged = []
            for entry in games:
                if not isinstance(entry, dict) or not entry.get("exe"):
                    continue
                base = new_game(entry.get("name") or "Untitled", entry["exe"])
                base.update({k: v for k, v in entry.items() if k in base})
                merged.append(base)
            self._games = merged

    def save(self) -> None:
        with self._lock:
            payload = {"version": SCHEMA_VERSION, "games": self._games}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # -- reads ----------------------------------------------------------

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(game) for game in self._games]

    def get(self, game_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for game in self._games:
                if game["id"] == game_id:
                    return dict(game)
        return None

    def has_exe(self, exe: str) -> bool:
        target = os.path.normcase(os.path.abspath(exe))
        with self._lock:
            return any(
                os.path.normcase(os.path.abspath(game["exe"])) == target
                for game in self._games
            )

    # -- writes ---------------------------------------------------------

    def add(self, name: str, exe: str, **extra: Any) -> Dict[str, Any]:
        game = new_game(name, exe, **extra)
        with self._lock:
            self._games.append(game)
            self.save()
        return dict(game)

    def update(self, game_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            for game in self._games:
                if game["id"] != game_id:
                    continue
                for key, value in changes.items():
                    if key in EDITABLE_FIELDS:
                        game[key] = value
                self.save()
                return dict(game)
        return None

    def remove(self, game_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for index, game in enumerate(self._games):
                if game["id"] == game_id:
                    removed = self._games.pop(index)
                    self.save()
                    return dict(removed)
        return None

    def record_session(self, game_id: str, seconds: float) -> None:
        """Fold a finished play session into the game's totals."""
        with self._lock:
            for game in self._games:
                if game["id"] == game_id:
                    game["play_seconds"] = int(game.get("play_seconds", 0) + max(0, seconds))
                    self.save()
                    return

    def mark_launched(self, game_id: str) -> None:
        with self._lock:
            for game in self._games:
                if game["id"] == game_id:
                    game["last_played"] = _now()
                    game["play_count"] = int(game.get("play_count", 0)) + 1
                    self.save()
                    return
