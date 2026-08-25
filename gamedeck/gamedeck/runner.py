"""Starting games and keeping track of the ones that are running."""

import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class LaunchError(RuntimeError):
    pass


def _split_args(args: str) -> List[str]:
    if not args:
        return []
    # Windows paths are full of backslashes; posix=False stops shlex eating them.
    return shlex.split(args, posix=(sys.platform != "win32"))


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    # Give the game its own process group so Ctrl+C in the GameDeck window
    # never reaches it, and keep our console out of its way.
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class Runner:
    """Launches games and folds finished sessions back into the library."""

    POLL_SECONDS = 2.0

    def __init__(self, library):
        self.library = library
        self._lock = threading.Lock()
        self._running: Dict[str, Dict[str, Any]] = {}
        self._stop_event = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    # -- launching ------------------------------------------------------

    def launch(self, game: Dict[str, Any]) -> Dict[str, Any]:
        game_id = game["id"]
        with self._lock:
            if game_id in self._running:
                raise LaunchError(f"{game['name']} is already running.")

        exe = Path(game["exe"]).expanduser()
        if not exe.exists():
            raise LaunchError(f"Executable not found: {exe}")

        workdir = game.get("cwd") or str(exe.parent)
        if not Path(workdir).is_dir():
            workdir = str(exe.parent)

        started = time.time()

        if exe.suffix.lower() == ".lnk":
            # Shortcuts have to go through the shell, which means no handle to
            # poll -- the game runs, but playtime cannot be measured.
            if not hasattr(os, "startfile"):
                raise LaunchError("Shortcuts (.lnk) can only be launched on Windows.")
            os.startfile(str(exe))  # noqa: S606 - user-chosen local shortcut
            self.library.mark_launched(game_id)
            return {"tracked": False, "started": started}

        try:
            process = subprocess.Popen(
                [str(exe), *_split_args(game.get("args", ""))],
                cwd=workdir,
                creationflags=_creation_flags(),
                close_fds=True,
            )
        except OSError as error:
            raise LaunchError(f"Could not start {exe.name}: {error}") from error

        with self._lock:
            self._running[game_id] = {
                "process": process,
                "started": started,
                "name": game["name"],
            }
        self.library.mark_launched(game_id)
        return {"tracked": True, "started": started, "pid": process.pid}

    def stop(self, game_id: str) -> bool:
        with self._lock:
            entry = self._running.get(game_id)
        if not entry:
            return False

        process = entry["process"]
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        self._finish(game_id)
        return True

    # -- state ----------------------------------------------------------

    def running_ids(self) -> List[str]:
        with self._lock:
            return list(self._running)

    def session_seconds(self, game_id: str) -> Optional[float]:
        with self._lock:
            entry = self._running.get(game_id)
            return time.time() - entry["started"] if entry else None

    def status(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        with self._lock:
            return {
                game_id: {"seconds": now - entry["started"], "pid": entry["process"].pid}
                for game_id, entry in self._running.items()
            }

    # -- internals ------------------------------------------------------

    def _finish(self, game_id: str) -> None:
        with self._lock:
            entry = self._running.pop(game_id, None)
        if entry:
            self.library.record_session(game_id, time.time() - entry["started"])

    def _reap_loop(self) -> None:
        while not self._stop_event.wait(self.POLL_SECONDS):
            with self._lock:
                finished = [
                    game_id
                    for game_id, entry in self._running.items()
                    if entry["process"].poll() is not None
                ]
            for game_id in finished:
                self._finish(game_id)

    def shutdown(self) -> None:
        """Record playtime for anything still open when GameDeck closes."""
        self._stop_event.set()
        for game_id in self.running_ids():
            self._finish(game_id)
