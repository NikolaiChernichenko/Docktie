# Packaging Docktie

Docktie is the menu-bar application. The Python package remains `docklock`. A standalone bundle is built with py2app and can be copied to `/Applications`.

The bundle is a menu-bar agent (`LSUIElement`). It does not show an icon in the Dock. Finder uses `packaging/Docktie.icns`.

## Build

From the project directory, with the existing virtualenv:

```bash
.venv/bin/pip install py2app
.venv/bin/python setup.py py2app
/usr/libexec/PlistBuddy -c 'Delete :NSMainNibFile' dist/Docktie.app/Contents/Info.plist
codesign --force --deep --sign - dist/Docktie.app
```

py2app leaves `NSMainNibFile` set to `MainMenu`, and this app has no nib. Delete that key before signing.

The finished bundle is `dist/Docktie.app`. Copy that folder to `/Applications` and open it from Finder.

Rebuild after code or icon changes. `build/` is an intermediate directory.

## First launch

macOS ties Accessibility permission to the application file. Grant Docktie access in System Settings, Privacy & Security, Accessibility. Permission given earlier to the terminal Python does not carry over.

The app stores settings in `~/Library/Application Support/DockLockClone/settings.json`.
