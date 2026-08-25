"""Where GameDeck keeps its data.

The library lives outside the repo so that reinstalling or moving GameDeck
never touches your games list.
"""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Directory holding library.json and cover art.

    Override with the GAMEDECK_HOME environment variable -- useful for keeping
    a portable library on the same drive as the games.
    """
    override = os.environ.get("GAMEDECK_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        path = Path(base) / "GameDeck"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "GameDeck"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        path = Path(base) / "gamedeck"

    path.mkdir(parents=True, exist_ok=True)
    return path


def library_file() -> Path:
    return data_dir() / "library.json"


def covers_dir() -> Path:
    path = data_dir() / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def web_dir() -> Path:
    return Path(__file__).resolve().parent / "web"
