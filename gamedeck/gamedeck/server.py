"""The local web server behind the GameDeck UI.

It only ever listens on the loopback interface, and because its API can start
programs, every request is checked twice: the Host header must be loopback (so
a rebound DNS name cannot reach it) and requests must carry the session token
that is baked into the page at startup (so another site in your browser cannot
drive it).
"""

import base64
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from . import dialogs, paths, scanner
from .library import Library
from .runner import LaunchError, Runner

MAX_BODY_BYTES = 12 * 1024 * 1024  # cover art uploads dominate this
ALLOWED_COVER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class GameDeckApp:
    """Everything the request handler needs, in one place."""

    def __init__(self) -> None:
        self.library = Library(paths.library_file())
        self.runner = Runner(self.library)
        self.covers = paths.covers_dir()
        self.web = paths.web_dir()
        self.token = secrets.token_urlsafe(24)

    # -- views ----------------------------------------------------------

    def game_view(self, game: Dict[str, Any]) -> Dict[str, Any]:
        view = dict(game)
        cover = game.get("cover")
        if cover:
            cover_path = self.covers / cover
            if cover_path.exists():
                view["cover_url"] = f"/covers/{cover}?v={int(cover_path.stat().st_mtime)}"
            else:
                view["cover_url"] = None
        else:
            view["cover_url"] = None
        view["exists"] = os.path.exists(os.path.expanduser(game["exe"]))
        session = self.runner.session_seconds(game["id"])
        view["running"] = session is not None
        view["session_seconds"] = session
        return view

    def state(self) -> Dict[str, Any]:
        return {
            "games": [self.game_view(game) for game in self.library.all()],
            "now": time.time(),
        }

    # -- actions --------------------------------------------------------

    def add_game(self, body: Dict[str, Any]) -> Dict[str, Any]:
        exe = (body.get("exe") or "").strip()
        if not exe:
            raise ApiError("An executable path is required.")
        exe = os.path.expanduser(exe)
        if not os.path.exists(exe):
            raise ApiError(f"No such file: {exe}")
        if self.library.has_exe(exe):
            raise ApiError("That executable is already in your library.")

        name = (body.get("name") or "").strip() or scanner.pretty_name(Path(exe).stem)
        game = self.library.add(
            name,
            exe,
            args=(body.get("args") or "").strip(),
            cwd=body.get("cwd") or None,
        )
        return self.game_view(game)

    def update_game(self, game_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        if "name" in body:
            name = (body["name"] or "").strip()
            if not name:
                raise ApiError("A game needs a name.")
            changes["name"] = name
        if "exe" in body:
            exe = os.path.expanduser((body["exe"] or "").strip())
            if not exe or not os.path.exists(exe):
                raise ApiError(f"No such file: {exe}")
            changes["exe"] = exe
        for field in ("args", "notes"):
            if field in body:
                changes[field] = (body[field] or "").strip()
        if "cwd" in body:
            changes["cwd"] = (body["cwd"] or "").strip() or None
        if "favorite" in body:
            changes["favorite"] = bool(body["favorite"])

        game = self.library.update(game_id, changes)
        if not game:
            raise ApiError("Game not found.", 404)
        return self.game_view(game)

    def delete_game(self, game_id: str) -> Dict[str, Any]:
        game = self.library.remove(game_id)
        if not game:
            raise ApiError("Game not found.", 404)
        if game.get("cover"):
            try:
                (self.covers / game["cover"]).unlink()
            except OSError:
                pass
        return {"removed": game_id}

    def launch_game(self, game_id: str) -> Dict[str, Any]:
        game = self.library.get(game_id)
        if not game:
            raise ApiError("Game not found.", 404)
        try:
            self.runner.launch(game)
        except LaunchError as error:
            raise ApiError(str(error)) from error
        return self.game_view(self.library.get(game_id) or game)

    def stop_game(self, game_id: str) -> Dict[str, Any]:
        if not self.runner.stop(game_id):
            raise ApiError("That game is not running.")
        game = self.library.get(game_id)
        if not game:
            raise ApiError("Game not found.", 404)
        return self.game_view(game)

    def scan_folder(self, body: Dict[str, Any]) -> Dict[str, Any]:
        folder = (body.get("path") or "").strip()
        if not folder:
            raise ApiError("Choose a folder to scan.")
        try:
            found = scanner.scan(os.path.expanduser(folder))
        except NotADirectoryError as error:
            raise ApiError(str(error)) from error
        except OSError as error:
            raise ApiError(f"Could not read that folder: {error}") from error

        for candidate in found:
            candidate["already_added"] = self.library.has_exe(candidate["exe"])
        return {"candidates": found}

    def set_cover(self, game_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        game = self.library.get(game_id)
        if not game:
            raise ApiError("Game not found.", 404)

        source_path = (body.get("path") or "").strip()
        data_url = body.get("data") or ""

        if source_path:
            source = Path(os.path.expanduser(source_path))
            if not source.is_file():
                raise ApiError(f"No such image: {source}")
            suffix = source.suffix.lower()
            if suffix not in ALLOWED_COVER_SUFFIXES:
                raise ApiError("Cover art must be a PNG, JPG, WEBP or GIF.")
            target = self.covers / f"{game_id}{suffix}"
            shutil.copyfile(source, target)
        elif data_url:
            match = re.match(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", data_url, re.S)
            if not match:
                raise ApiError("That image format is not supported.")
            suffix = "." + ("jpg" if match.group(1) == "jpeg" else match.group(1))
            try:
                blob = base64.b64decode(match.group(2), validate=True)
            except (ValueError, TypeError) as error:
                raise ApiError("The image could not be decoded.") from error
            target = self.covers / f"{game_id}{suffix}"
            target.write_bytes(blob)
        else:
            # No source given: clear the cover.
            if game.get("cover"):
                try:
                    (self.covers / game["cover"]).unlink()
                except OSError:
                    pass
            return self.game_view(self.library.update(game_id, {"cover": None}) or game)

        # Drop any cover left behind under a different extension.
        for stale in self.covers.glob(f"{game_id}.*"):
            if stale != target:
                try:
                    stale.unlink()
                except OSError:
                    pass

        updated = self.library.update(game_id, {"cover": target.name})
        return self.game_view(updated or game)

    def browse(self, body: Dict[str, Any]) -> Dict[str, Any]:
        kind = body.get("kind") or "file"
        if kind not in ("file", "folder", "image"):
            raise ApiError("Unknown picker type.")
        path, error = dialogs.pick(kind)
        if error:
            raise ApiError(error)
        return {"path": path}


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = "GameDeck"
    protocol_version = "HTTP/1.1"

    app: GameDeckApp  # set on the server instance

    # -- plumbing -------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if os.environ.get("GAMEDECK_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("Malformed request.")
        if length > MAX_BODY_BYTES:
            raise ApiError("That file is too large (12 MB max).", 413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ApiError("Malformed request body.") from error
        if not isinstance(body, dict):
            raise ApiError("Malformed request body.")
        return body

    def _check_origin(self) -> None:
        """Reject anything not addressed to us over loopback."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host and host.lower() not in LOOPBACK_HOSTS:
            raise ApiError("Refused: GameDeck only answers on localhost.", 403)

        origin = self.headers.get("Origin")
        if origin:
            hostname = urlparse(origin).hostname or ""
            if hostname.lower() not in LOOPBACK_HOSTS:
                raise ApiError("Refused: cross-site request.", 403)

    def _check_token(self) -> None:
        if not secrets.compare_digest(
            self.headers.get("X-GameDeck-Token") or "", self.app.token
        ):
            raise ApiError("Stale page -- reload GameDeck.", 403)

    # -- verbs ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            self._check_origin()
            if path.startswith("/api/"):
                self._check_token()
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                raise ApiError("Not found.", 404)
        except ApiError as error:
            self._send_json(error.status, {"error": error.message})
        except BrokenPipeError:
            pass
        except Exception as error:  # a bug here should not kill the server
            self._send_json(500, {"error": f"Unexpected error: {error}"})

    # -- routes ---------------------------------------------------------

    def _api(self, method: str, path: str) -> None:
        app = self.app
        game_route = re.match(r"^/api/games/([A-Za-z0-9]+)(/[a-z]+)?$", path)

        if path == "/api/state" and method == "GET":
            return self._send_json(200, app.state())

        if path == "/api/games" and method == "POST":
            return self._send_json(200, app.add_game(self._read_json()))

        if path == "/api/scan" and method == "POST":
            return self._send_json(200, app.scan_folder(self._read_json()))

        if path == "/api/browse" and method == "POST":
            return self._send_json(200, app.browse(self._read_json()))

        if path == "/api/reveal" and method == "POST":
            return self._send_json(200, self._reveal(self._read_json()))

        if game_route:
            game_id, action = game_route.group(1), game_route.group(2)
            if action is None and method == "PATCH":
                return self._send_json(200, app.update_game(game_id, self._read_json()))
            if action is None and method == "DELETE":
                return self._send_json(200, app.delete_game(game_id))
            if action == "/launch" and method == "POST":
                return self._send_json(200, app.launch_game(game_id))
            if action == "/stop" and method == "POST":
                return self._send_json(200, app.stop_game(game_id))
            if action == "/cover" and method == "POST":
                return self._send_json(200, app.set_cover(game_id, self._read_json()))

        raise ApiError("Not found.", 404)

    def _reveal(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Open a game's folder in the system file manager."""
        game = self.app.library.get(body.get("id") or "")
        if not game:
            raise ApiError("Game not found.", 404)
        folder = Path(os.path.expanduser(game["exe"])).parent
        if not folder.is_dir():
            raise ApiError("That folder no longer exists.")
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(folder))  # noqa: S606 - user's own game folder
            else:
                opener = shutil.which("xdg-open") or shutil.which("open")
                if not opener:
                    raise ApiError("No file manager available.")
                os.spawnl(os.P_NOWAIT, opener, opener, str(folder))
        except OSError as error:
            raise ApiError(f"Could not open the folder: {error}") from error
        return {"opened": str(folder)}

    # -- static ---------------------------------------------------------

    def _static(self, path: str) -> None:
        app = self.app
        if path in ("/", "/index.html"):
            page = (app.web / "index.html").read_text(encoding="utf-8")
            page = page.replace("__GAMEDECK_TOKEN__", app.token)
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        if path.startswith("/covers/"):
            return self._send_file(app.covers, path[len("/covers/"):])

        return self._send_file(app.web, path.lstrip("/"))

    def _send_file(self, root: Path, relative: str) -> None:
        if not relative:
            raise ApiError("Not found.", 404)
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            raise ApiError("Not found.", 404)  # path traversal attempt
        if not target.is_file():
            raise ApiError("Not found.", 404)
        self._send(200, target.read_bytes(), _content_type(target))


def build_server(port: int = 0) -> Tuple[ThreadingHTTPServer, GameDeckApp]:
    app = GameDeckApp()
    handler: Callable[..., BaseHTTPRequestHandler] = type(
        "BoundHandler", (Handler,), {"app": app}
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server, app
