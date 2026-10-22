# Docktie

Docktie is a macOS menu-bar app that keeps the Dock on the displays you choose, so it does not follow the cursor to another screen.

The first allowed display is home. Docktie leaves the Dock on any allowed display, and moves it back when it leaves that set. After wake, startup, or a display-arrangement change it returns the Dock home. Hold a modifier key to move the Dock by hand.

The Dock has to sit on the bottom edge. macOS also needs **Displays have separate Spaces** turned on.

## Requirements

- macOS
- Python 3.14
- Accessibility permission for Docktie

## Run from source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m docklock
```

Quit from the menu-bar icon.

## Application bundle

`docs/packaging.md` explains how to build `dist/Docktie.app` and copy it to `/Applications`.

## Settings

Settings are stored in `~/Library/Application Support/DockLockClone/settings.json`.

Set `DOCKLOCK_DEBUG=1` to print whether a move reported success.
