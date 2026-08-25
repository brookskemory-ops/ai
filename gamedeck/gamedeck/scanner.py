"""Find games in a folder.

Point this at wherever you unzip your downloads and it works out which .exe in
each game folder is the one you actually want to double-click -- skipping the
uninstallers, redistributables and crash handlers that ship alongside it.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_DEPTH = 4

EXECUTABLE_SUFFIXES = (".exe", ".bat", ".cmd", ".lnk")

# Filenames that are never the game itself.
NOISE_PATTERNS = (
    r"^unins",
    r"^setup",
    r"^install",
    r"^vc_?redist",
    r"^vcredist",
    r"^dxsetup",
    r"^dxwebsetup",
    r"^directx",
    r"^oalinst",
    r"^dotnetfx",
    r"^ndp\d",
    r"^python",
    r"^pythonw",
    r"^ue\d?prereqsetup",
    r"crashhandler",
    r"crashreport",
    r"crashpad",
    r"^epicwebhelper",
    r"^notification_helper",
    r"^subprocess",
    r"^cefsub",
    r"^quickbms",
    r"^7z",
    r"^winrar",
    r"^readme",
    r"^config$",
    r"^settings$",
    r"^benchmark",
    r"^dedicated",
    r"^server$",
    r"^tools?$",
    r"^editor$",
)

# Folders worth descending into for the real binary, and folders to skip.
NOISE_DIRS = {
    "_commonredist",
    "commonredist",
    "redist",
    "redistributable",
    "redistributables",
    "directx",
    "dotnet",
    "vcredist",
    "$recycle.bin",
    "system volume information",
    "node_modules",
    ".git",
}

_NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)


def is_noise(exe_path: str) -> bool:
    return bool(_NOISE_RE.search(Path(exe_path).stem))


def pretty_name(raw: str) -> str:
    """Turn 'Some.Game-Name_v1.2 [FitGirl]' into 'Some Game Name'."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", raw)
    # Strip versions before separators, while the dots that mark them survive.
    name = re.sub(r"\bv?\d+(\.\d+)+[a-z]?\b", " ", name)
    name = re.sub(r"[._-]+", " ", name)
    name = re.sub(
        r"\b(repack|multi\d*|proper|final|gog|codex|plaza|win\d{2}|x64|x86|"
        r"full|edition|release|build)\b",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"\s+", " ", name).strip(" -")
    return name or raw


def _similarity(stem: str, folder: str) -> float:
    """Cheap token overlap between an exe name and its folder name."""
    def tokens(text: str) -> set:
        return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1}

    a, b = tokens(stem), tokens(folder)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score(exe: Path, root: Path, folder_name: str) -> float:
    """Higher is more likely to be the game's main executable."""
    try:
        size_mb = exe.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0.0

    depth = len(exe.relative_to(root).parts) - 1
    stem = exe.stem

    score = 0.0
    score += 60 * _similarity(stem, folder_name)
    score += min(size_mb, 40) * 0.5          # big binaries beat tiny helpers
    score -= depth * 6                        # shallower is better
    if exe.suffix.lower() == ".exe":
        score += 5
    if "shipping" in stem.lower():            # Unreal's real entry point
        score += 12
    if re.search(r"\b(launch|launcher|start|play)\b", stem, re.IGNORECASE):
        score += 8
    if is_noise(str(exe)):
        score -= 100
    return score


def _executables_in(folder: Path) -> List[Path]:
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        current = Path(dirpath)
        depth = len(current.relative_to(folder).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d.lower() not in NOISE_DIRS]
        for filename in filenames:
            if filename.lower().endswith(EXECUTABLE_SUFFIXES):
                found.append(current / filename)
        if len(found) > 400:  # pathological folder; stop before it hurts
            break
    return found


def _candidate_for(folder: Path) -> Optional[Dict[str, Any]]:
    executables = _executables_in(folder)
    if not executables:
        return None

    folder_name = folder.name
    ranked = sorted(
        executables, key=lambda exe: _score(exe, folder, folder_name), reverse=True
    )
    best = ranked[0]
    if is_noise(str(best)):
        return None  # nothing here but installers

    alternates = [str(path) for path in ranked[1:12] if not is_noise(str(path))]
    return {
        "name": pretty_name(folder_name),
        "exe": str(best),
        "folder": str(folder),
        "alternates": alternates,
    }


def scan(root: str) -> List[Dict[str, Any]]:
    """Return one candidate game per sub-folder of `root`.

    If `root` itself is a game folder (it has executables directly inside), it
    is returned as the single candidate.
    """
    base = Path(root).expanduser()
    if not base.is_dir():
        raise NotADirectoryError(f"Not a folder: {base}")

    direct = [
        entry
        for entry in base.iterdir()
        if entry.is_file() and entry.name.lower().endswith(EXECUTABLE_SUFFIXES)
    ]
    if direct:
        candidate = _candidate_for(base)
        return [candidate] if candidate else []

    candidates: List[Dict[str, Any]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.lower() in NOISE_DIRS:
            continue
        candidate = _candidate_for(entry)
        if candidate:
            candidates.append(candidate)
    return candidates
