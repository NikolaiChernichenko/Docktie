"""Build the Docktie menu-bar application bundle."""

from setuptools import setup

APP = ["docktie.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "packaging/Docktie.icns",
    "packages": ["docklock"],
    "plist": {
        "CFBundleName": "Docktie",
        "CFBundleDisplayName": "Docktie",
        "CFBundleIdentifier": "local.docktie",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="Docktie",
    app=APP,
    options={"py2app": OPTIONS},
)
