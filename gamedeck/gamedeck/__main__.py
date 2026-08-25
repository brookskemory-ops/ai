"""Start GameDeck: `python -m gamedeck`."""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from errno import EADDRINUSE

from . import __version__, paths
from .server import build_server

DEFAULT_PORT = 8777

# Browsers that can open a page as its own chrome-less window, so GameDeck
# looks like an app rather than a tab.
APP_MODE_BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _app_mode_browser() -> str:
    for candidate in APP_MODE_BROWSERS:
        if os.path.exists(candidate):
            return candidate
    for name in ("chromium", "google-chrome", "chromium-browser", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def open_ui(url: str, window: bool) -> None:
    browser = _app_mode_browser() if window else ""
    if browser:
        try:
            subprocess.Popen(
                [browser, f"--app={url}", "--window-size=1280,860"], close_fds=True
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gamedeck", description="A Steam-style launcher for your local games."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="start the server without opening the UI")
    parser.add_argument("--tab", action="store_true",
                        help="open in a normal browser tab instead of its own window")
    parser.add_argument("--version", action="version", version=f"GameDeck {__version__}")
    args = parser.parse_args(argv)

    try:
        server, app = build_server(args.port)
    except OSError as error:
        if error.errno != EADDRINUSE:
            raise
        print(f"Port {args.port} is busy; picking a free one.")
        server, app = build_server(0)

    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"GameDeck {__version__}")
    print(f"  library : {paths.library_file()}")
    print(f"  open at : {url}")
    print("  press Ctrl+C to quit")

    if not args.no_browser:
        threading.Timer(0.4, open_ui, args=(url, not args.tab)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        app.runner.shutdown()
        server.shutdown()
        server.server_close()
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
