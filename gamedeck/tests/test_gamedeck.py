"""Tests for GameDeck: library storage, executable detection, and the HTTP API."""

import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamedeck import scanner  # noqa: E402
from gamedeck.library import Library  # noqa: E402
from gamedeck.runner import LaunchError, Runner  # noqa: E402


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

def test_library_round_trip(tmp_path):
    path = tmp_path / "library.json"
    library = Library(path)
    game = library.add("Hollow Knight", str(tmp_path / "hk.exe"), args="-windowed")

    reloaded = Library(path)
    stored = reloaded.get(game["id"])
    assert stored["name"] == "Hollow Knight"
    assert stored["args"] == "-windowed"
    assert stored["play_seconds"] == 0


def test_library_records_playtime_and_launches(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Celeste", str(tmp_path / "celeste.exe"))

    library.mark_launched(game["id"])
    library.record_session(game["id"], 90.4)
    library.record_session(game["id"], 30.0)

    updated = library.get(game["id"])
    assert updated["play_seconds"] == 120
    assert updated["play_count"] == 1
    assert updated["last_played"] is not None


def test_library_ignores_client_owned_playtime(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Dusk", str(tmp_path / "dusk.exe"))

    library.update(game["id"], {"play_seconds": 999999, "name": "DUSK"})

    updated = library.get(game["id"])
    assert updated["play_seconds"] == 0
    assert updated["name"] == "DUSK"


def test_corrupt_library_is_set_aside_not_lost(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{ this is not json", encoding="utf-8")

    library = Library(path)

    assert library.all() == []
    assert path.with_suffix(".corrupt.json").exists()


def test_remove_returns_none_for_unknown_game(tmp_path):
    library = Library(tmp_path / "library.json")
    assert library.remove("nope") is None


# --------------------------------------------------------------------------
# scanner
# --------------------------------------------------------------------------

def _make(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_scan_picks_the_game_over_the_uninstaller(tmp_path):
    folder = tmp_path / "Hollow Knight"
    _make(folder / "unins000.exe", 5_000_000)
    _make(folder / "hollow_knight.exe", 2_000_000)
    _make(folder / "_CommonRedist" / "vcredist_x64.exe", 9_000_000)

    [candidate] = scanner.scan(str(tmp_path))

    assert Path(candidate["exe"]).name == "hollow_knight.exe"
    assert candidate["name"] == "Hollow Knight"


def test_scan_prefers_unreal_shipping_binary(tmp_path):
    folder = tmp_path / "Deep Rock"
    _make(folder / "Engine" / "Extras" / "helper.exe", 100)
    _make(folder / "DeepRock-Win64-Shipping.exe", 4_000_000)

    [candidate] = scanner.scan(str(tmp_path))

    assert Path(candidate["exe"]).name == "DeepRock-Win64-Shipping.exe"


def test_scan_reports_each_game_folder(tmp_path):
    _make(tmp_path / "Celeste" / "Celeste.exe", 1_000_000)
    _make(tmp_path / "Dusk" / "Dusk.exe", 1_000_000)
    _make(tmp_path / "Redist" / "setup.exe", 1_000_000)

    names = sorted(c["name"] for c in scanner.scan(str(tmp_path)))

    assert names == ["Celeste", "Dusk"]


def test_scan_accepts_a_single_game_folder(tmp_path):
    _make(tmp_path / "Stardew Valley.exe", 1_000_000)

    candidates = scanner.scan(str(tmp_path))

    assert len(candidates) == 1
    assert Path(candidates[0]["exe"]).name == "Stardew Valley.exe"


def test_scan_rejects_a_file(tmp_path):
    target = _make(tmp_path / "game.exe")
    with pytest.raises(NotADirectoryError):
        scanner.scan(str(target))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hollow.Knight-v1.5.78", "Hollow Knight"),
        ("Celeste_[FitGirl Repack]", "Celeste"),
        ("DUSK win64 x64", "DUSK"),
    ],
)
def test_pretty_name(raw, expected):
    assert scanner.pretty_name(raw) == expected


def test_noise_detection():
    assert scanner.is_noise("C:/Games/X/unins000.exe")
    assert scanner.is_noise("C:/Games/X/UnityCrashHandler64.exe")
    assert not scanner.is_noise("C:/Games/X/hollow_knight.exe")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

def _fake_game(tmp_path, seconds="5"):
    """A tiny executable that just sleeps, standing in for a game."""
    if sys.platform == "win32":
        script = tmp_path / "game.bat"
        script.write_text(f"@echo off\r\nping -n {seconds} 127.0.0.1 >nul\r\n")
    else:
        script = tmp_path / "game.sh"
        script.write_text(f"#!/bin/sh\nsleep {seconds}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_runner_tracks_and_stops_a_game(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Sleeper", str(_fake_game(tmp_path)))
    runner = Runner(library)

    try:
        runner.launch(game)
        assert runner.running_ids() == [game["id"]]
        assert runner.session_seconds(game["id"]) is not None

        assert runner.stop(game["id"]) is True
        assert runner.running_ids() == []
        assert library.get(game["id"])["play_count"] == 1
    finally:
        runner.shutdown()


def test_runner_refuses_a_second_copy(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Sleeper", str(_fake_game(tmp_path)))
    runner = Runner(library)

    try:
        runner.launch(game)
        with pytest.raises(LaunchError):
            runner.launch(game)
    finally:
        runner.stop(game["id"])
        runner.shutdown()


def test_runner_reports_a_missing_executable(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Ghost", str(tmp_path / "gone.exe"))
    runner = Runner(library)

    try:
        with pytest.raises(LaunchError, match="not found"):
            runner.launch(game)
    finally:
        runner.shutdown()


def test_runner_records_playtime_when_a_game_exits(tmp_path):
    library = Library(tmp_path / "library.json")
    game = library.add("Quick", str(_fake_game(tmp_path, seconds="1")))
    runner = Runner(library)
    runner.POLL_SECONDS = 0.2

    try:
        runner.launch(game)
        deadline = time.time() + 15
        while runner.running_ids() and time.time() < deadline:
            time.sleep(0.2)
        assert runner.running_ids() == []
    finally:
        runner.shutdown()


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

@pytest.fixture
def live_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEDECK_HOME", str(tmp_path / "home"))
    from gamedeck.server import build_server

    server, app = build_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, app
    finally:
        app.runner.shutdown()
        server.shutdown()
        server.server_close()


def _request(base, method, path, body=None, token=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Host", urllib.parse.urlparse(base).netloc)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-GameDeck-Token", token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode())


def test_api_requires_the_session_token(live_server):
    base, _ = live_server
    with pytest.raises(urllib.error.HTTPError) as caught:
        _request(base, "GET", "/api/state")
    assert caught.value.code == 403


def test_api_rejects_cross_site_requests(live_server):
    base, app = live_server
    with pytest.raises(urllib.error.HTTPError) as caught:
        _request(
            base, "GET", "/api/state",
            token=app.token,
            headers={"Origin": "https://evil.example"},
        )
    assert caught.value.code == 403


def test_index_page_carries_the_token(live_server):
    base, app = live_server
    with urllib.request.urlopen(base + "/", timeout=10) as response:
        page = response.read().decode()
    assert app.token in page
    assert "__GAMEDECK_TOKEN__" not in page


def test_add_launch_and_remove_over_the_api(live_server, tmp_path):
    base, app = live_server
    exe = _fake_game(tmp_path)

    status, game = _request(
        base, "POST", "/api/games", {"exe": str(exe), "name": "Sleeper"}, app.token
    )
    assert status == 200
    assert game["name"] == "Sleeper"
    assert game["exists"] is True

    _, launched = _request(base, "POST", f"/api/games/{game['id']}/launch", {}, app.token)
    assert launched["running"] is True

    _, state = _request(base, "GET", "/api/state", token=app.token)
    assert [g["id"] for g in state["games"]] == [game["id"]]

    _, stopped = _request(base, "POST", f"/api/games/{game['id']}/stop", {}, app.token)
    assert stopped["running"] is False

    _, removed = _request(base, "DELETE", f"/api/games/{game['id']}", token=app.token)
    assert removed["removed"] == game["id"]
    assert _request(base, "GET", "/api/state", token=app.token)[1]["games"] == []


def test_api_rejects_a_duplicate_and_a_missing_exe(live_server, tmp_path):
    base, app = live_server
    exe = _fake_game(tmp_path)
    _request(base, "POST", "/api/games", {"exe": str(exe)}, app.token)

    for body in ({"exe": str(exe)}, {"exe": str(tmp_path / "nope.exe")}):
        with pytest.raises(urllib.error.HTTPError) as caught:
            _request(base, "POST", "/api/games", body, app.token)
        assert caught.value.code == 400


def test_cover_upload_is_stored_and_served(live_server, tmp_path):
    base, app = live_server
    exe = _fake_game(tmp_path)
    _, game = _request(base, "POST", "/api/games", {"exe": str(exe)}, app.token)

    # A 1x1 transparent PNG.
    png = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        "C0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    _, updated = _request(
        base, "POST", f"/api/games/{game['id']}/cover", {"data": png}, app.token
    )
    assert updated["cover_url"].startswith(f"/covers/{game['id']}.png")

    with urllib.request.urlopen(base + updated["cover_url"], timeout=10) as response:
        assert response.headers["Content-Type"] == "image/png"
        assert response.read()[:8] == b"\x89PNG\r\n\x1a\n"


def test_static_files_cannot_escape_their_folder(live_server):
    base, _ = live_server
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(base + "/covers/..%2F..%2Flibrary.json", timeout=10)
    assert caught.value.code == 404


def test_scan_endpoint_finds_games(live_server, tmp_path):
    base, app = live_server
    games = tmp_path / "Games"
    _make(games / "Celeste" / "Celeste.exe", 1_000_000)

    _, payload = _request(base, "POST", "/api/scan", {"path": str(games)}, app.token)

    assert [c["name"] for c in payload["candidates"]] == ["Celeste"]
    assert payload["candidates"][0]["already_added"] is False


def test_library_lives_under_gamedeck_home(live_server, tmp_path):
    _, app = live_server
    assert str(tmp_path / "home") in str(app.library.path)
    assert os.path.isdir(tmp_path / "home")
