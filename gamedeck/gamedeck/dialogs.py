"""Native "Browse..." dialogs.

The browser deliberately hides real file paths from web pages, so picking a
game's .exe happens on the Python side. Tk dislikes being driven from a worker
thread, so each dialog runs in a short-lived child process instead.
"""

import json
import subprocess
import sys
from typing import List, Optional, Tuple

_PICKER = r"""
import json, sys
import tkinter as tk
from tkinter import filedialog

kind = sys.argv[1]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if kind == "folder":
    result = filedialog.askdirectory(title="Choose a folder of games")
elif kind == "image":
    result = filedialog.askopenfilename(
        title="Choose cover art",
        filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("All files", "*.*")],
    )
else:
    result = filedialog.askopenfilename(
        title="Choose a game executable",
        filetypes=[("Programs", "*.exe *.bat *.cmd *.lnk"), ("All files", "*.*")],
    )
root.destroy()
print(json.dumps({"path": result or ""}))
"""


def _python_with_console() -> str:
    """pythonw.exe cannot be used for the child: it has no usable stdout."""
    executable = sys.executable
    if sys.platform == "win32" and executable.lower().endswith("pythonw.exe"):
        return executable[: -len("pythonw.exe")] + "python.exe"
    return executable


def pick(kind: str = "file", timeout: float = 300.0) -> Tuple[Optional[str], Optional[str]]:
    """Show a native picker. Returns (path, error); both None means cancelled."""
    command: List[str] = [_python_with_console(), "-c", _PICKER, kind]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None, "The file picker timed out."
    except OSError as error:
        return None, f"Could not open a file picker: {error}"

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        hint = detail[-1] if detail else "the dialog failed to open"
        return None, f"File picker unavailable ({hint}). Paste the path instead."

    try:
        path = json.loads(completed.stdout.strip() or "{}").get("path") or None
    except json.JSONDecodeError:
        return None, "File picker returned nothing usable."
    return path, None
