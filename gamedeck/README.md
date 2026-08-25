# GameDeck

A Steam-style launcher for games you already have on disk. Point it at the
folder where your downloads live, and it gives you a library of cover-art tiles
you click to play — with play time, last played, launch options and notes kept
for each game.

It needs nothing but Python 3.8+. No pip install, no Node, no account.

```
gamedeck/
├── GameDeck.bat          double-click this on Windows
├── gamedeck/             the app
└── tests/                pytest suite
```

## Start it

**Windows** — double-click `GameDeck.bat`. It opens GameDeck in its own window
(Chrome or Edge app mode if you have either, otherwise a normal tab). Closing
the little black console window quits GameDeck.

If it says Python was not found, install it from
[python.org/downloads](https://www.python.org/downloads/) and tick
**"Add python.exe to PATH"** in the installer.

**Anything else** — from this folder:

```bash
python3 -m gamedeck
```

Useful flags: `--port 9000`, `--tab` (normal browser tab), `--no-browser`.

## Add your games

Click **Add games**:

- **Scan a folder** — give it the folder that *contains* your game folders
  (`C:\Games`, say, not `C:\Games\Hollow Knight`) and it looks inside each one
  and works out which `.exe` actually starts the game. Uninstallers,
  `vcredist`, DirectX setups and crash handlers are filtered out; for Unreal
  games it picks the `-Shipping.exe`. If it guesses wrong, the dropdown next to
  each result lists the other executables it found.
- **Add one game** — browse to a single `.exe` yourself.

`.bat`, `.cmd` and `.lnk` shortcuts work too. Shortcuts are launched through
Windows, which means play time can't be measured for them; `.exe` files are
tracked properly.

## Once games are in

- **Play** on the tile, or open a game and hit **Play**. The tile shows a live
  session timer while the game is up, and the button becomes **Stop**.
- **Cover art** — drag any image file onto a tile, or open the game and click
  **Set cover**. Steam's vertical capsules (600×900) fit perfectly. Without a
  cover you get a generated tile.
- **Launch options** go to the game as command-line arguments
  (`-windowed -skipintro`). **Working folder** only needs setting for the rare
  game that refuses to run from its own directory.
- **Filters** in the left rail: favorites, recently played, currently running,
  and *missing files* — games whose `.exe` has moved or been deleted.
- Press `/` to jump to the search box, `Esc` to close a panel.

Removing a game removes it from the library only. It never deletes your files.

## Where your data lives

| | |
|---|---|
| Windows | `%APPDATA%\GameDeck\` |
| macOS | `~/Library/Application Support/GameDeck/` |
| Linux | `~/.local/share/gamedeck/` |

`library.json` holds the games list, `covers/` the art. Set `GAMEDECK_HOME` to
put both somewhere else — handy for keeping the library on the same drive as
the games.

Reinstalling or moving GameDeck does not touch that folder. To back up your
library, copy it. If `library.json` is ever damaged it is renamed to
`library.corrupt.json` rather than thrown away.

## A note on how it's built

GameDeck is a small local web server plus a browser front end. That's what
makes the Steam-like UI cheap to build without a GUI toolkit, but it does mean
a page in your browser can start programs, so the server:

- listens on `127.0.0.1` only — nothing on your network can reach it,
- rejects requests whose `Host` or `Origin` header isn't loopback, which stops
  another website from talking to it behind your back,
- requires a random token, generated at startup and baked into the page, on
  every API call.

Reopening an old GameDeck tab after a restart gives "Stale page — reload
GameDeck". That's the token check doing its job; reload the page.

## Tests

```bash
python3 -m pytest tests -q
```

Covers library storage, the executable-detection heuristics, launching and
play-time accounting, and the HTTP API including its auth and path-traversal
defences.
