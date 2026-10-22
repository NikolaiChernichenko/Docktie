"""Menu-bar app that keeps the Dock on the displays you allow."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import Cocoa
import Quartz
import objc
from PyObjCTools import AppHelper

from docklock.dock_detect import DisplayInfo, DisplaySnapshot, capture_displays, locate_dock
from docklock.relocate import guard_cursor, install_bottom_guard, move_dock_to_display

SETTINGS_PATH = (
    Path.home() / "Library" / "Application Support" / "DockLockClone" / "settings.json"
)
# 2.0 and 5.0 are constants initialized by the original settings object.
# Their fields were not fully mapped, so they are used here as the dwell and
# the pause between attempts.
DEFAULT_DWELL_SECONDS = 2.0
RETRY_PAUSE_SECONDS = 5.0
WAKE_DELAY_SECONDS = 2.0
POLL_SECONDS = 0.4
GUARD_SECONDS = 0.05

MODIFIER_FLAGS = {
    "none": 0,
    "control": Cocoa.NSEventModifierFlagControl,
    "option": Cocoa.NSEventModifierFlagOption,
    "command": Cocoa.NSEventModifierFlagCommand,
    "shift": Cocoa.NSEventModifierFlagShift,
}


@dataclass
class Settings:
    lock_enabled: bool = True
    allowed_keys: list[str] | None = None
    modifier: str = "none"
    relocate_after_wake: bool = True
    relocate_after_display_change: bool = True
    dwell_seconds: float = DEFAULT_DWELL_SECONDS

    def __post_init__(self) -> None:
        if self.allowed_keys is None:
            self.allowed_keys = []
        if self.modifier not in MODIFIER_FLAGS:
            self.modifier = "none"

    @classmethod
    def load(cls) -> "Settings":
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            lock_enabled=bool(payload.get("lock_enabled", True)),
            allowed_keys=list(payload.get("allowed_keys", [])),
            modifier=str(payload.get("modifier", "none")),
            relocate_after_wake=bool(payload.get("relocate_after_wake", True)),
            relocate_after_display_change=bool(
                payload.get("relocate_after_display_change", True)
            ),
            dwell_seconds=float(payload.get("dwell_seconds", DEFAULT_DWELL_SECONDS)),
        )

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "lock_enabled": self.lock_enabled,
            "allowed_keys": self.allowed_keys,
            "modifier": self.modifier,
            "relocate_after_wake": self.relocate_after_wake,
            "relocate_after_display_change": self.relocate_after_display_change,
            "dwell_seconds": self.dwell_seconds,
        }
        SETTINGS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _debug(message: str) -> None:
    if os.environ.get("DOCKLOCK_DEBUG"):
        print(message, flush=True)


class AppDelegate(Cocoa.NSObject):
    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.settings = Settings.load()
        self._moving = False
        self._stop = threading.Event()
        self._cooldown_until = 0.0
        self._status = None
        self._settings_existed = SETTINGS_PATH.exists()
        self._guard_snapshot = None
        self._event_tap = None
        self._event_source = None
        self._event_callback = None
        return self

    def applicationDidFinishLaunching_(self, _notification) -> None:
        self._status = Cocoa.NSStatusBar.systemStatusBar().statusItemWithLength_(
            Cocoa.NSSquareStatusItemLength
        )
        button = self._status.button()
        image = Cocoa.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "lock.fill", "Docktie"
        )
        image = image.copy()
        image.setTemplate_(True)
        button.setImage_(image)
        button.setImagePosition_(Cocoa.NSImageOnly)
        menu = Cocoa.NSMenu.alloc().init()
        menu.setDelegate_(self)
        self._status.setMenu_(menu)

        workspace = Cocoa.NSWorkspace.sharedWorkspace()
        workspace.notificationCenter().addObserver_selector_name_object_(
            self,
            "didWake:",
            Cocoa.NSWorkspaceDidWakeNotification,
            None,
        )
        Cocoa.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self,
            "screensChanged:",
            Cocoa.NSApplicationDidChangeScreenParametersNotification,
            None,
        )
        Cocoa.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_SECONDS, self, "tick:", None, True
        )
        if not self._settings_existed:
            self._seed_allowed_displays()
        self._install_guard()
        self.performSelector_withObject_afterDelay_("enforceHome:", None, 1.0)

    def applicationWillTerminate_(self, _notification) -> None:
        self._stop.set()
        tap = self._event_tap
        if tap is not None:
            Quartz.CGEventTapEnable(tap, False)
            Quartz.CFMachPortInvalidate(tap)

    def menuNeedsUpdate_(self, menu) -> None:
        self._rebuild_menu(menu)

    def _seed_allowed_displays(self) -> None:
        snapshot = capture_displays()
        self.settings.allowed_keys = [display.key for display in snapshot.displays]
        self.settings.save()

    def _connected_allowed(self, snapshot: DisplaySnapshot) -> list[DisplayInfo]:
        by_key = {display.key: display for display in snapshot.displays}
        return [
            by_key[key] for key in self.settings.allowed_keys if key in by_key
        ]

    def _modifier_held(self) -> bool:
        flag = MODIFIER_FLAGS.get(self.settings.modifier, 0)
        if flag == 0:
            return False
        return (int(Cocoa.NSEvent.modifierFlags()) & int(flag)) != 0

    def _install_guard(self) -> None:
        self._guard_snapshot = capture_displays()
        try:
            tap, source, callback = install_bottom_guard(self._guard_decision)
        except Exception as exc:
            tap, source, callback = None, None, None
            _debug(f"bottom guard tap failed: {exc}")
        self._event_tap = tap
        self._event_source = source
        self._event_callback = callback
        _debug(f"bottom guard tap: {tap is not None}")
        Cocoa.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            GUARD_SECONDS, self, "guard:", None, True
        )

    def _guard_decision(self):
        if self._moving or not self.settings.lock_enabled or self._modifier_held():
            return None
        if self._guard_snapshot is None:
            return None
        return self._guard_snapshot, self.settings.allowed_keys

    def guard_(self, _timer) -> None:
        decision = self._guard_decision()
        if decision is None:
            return
        snapshot, allowed_keys = decision
        guard_cursor(snapshot, allowed_keys)

    def _rebuild_menu(self, menu) -> None:
        menu.removeAllItems()
        snapshot = capture_displays()
        dock = locate_dock(snapshot)
        dock_name = "not found"
        if dock.display_key:
            for display in snapshot.displays:
                if display.key == dock.display_key:
                    dock_name = display.name
                    break
        status = Cocoa.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Dock is on {dock_name}", None, ""
        )
        status.setEnabled_(False)
        menu.addItem_(status)
        menu.addItem_(Cocoa.NSMenuItem.separatorItem())

        lock_item = self._menu_item(
            "Lock Dock to allowed displays", "toggleLock:", self.settings.lock_enabled
        )
        menu.addItem_(lock_item)

        allow_menu = Cocoa.NSMenu.alloc().init()
        allowed = set(self.settings.allowed_keys)
        home_key = next(iter(self.settings.allowed_keys), None)
        for display in snapshot.displays:
            title = display.name
            if display.key == home_key:
                title = f"{display.name} (home)"
            allow_item = self._menu_item(
                title, "toggleDisplay:", display.key in allowed
            )
            allow_item.setRepresentedObject_(display.key)
            allow_menu.addItem_(allow_item)

        menu.addItem_(self._make_submenu("Allowed displays", allow_menu))
        menu.addItem_(self._make_submenu("Pause while holding", self._modifier_menu()))
        menu.addItem_(Cocoa.NSMenuItem.separatorItem())
        menu.addItem_(
            self._menu_item(
                "Return home after wake",
                "toggleWake:",
                self.settings.relocate_after_wake,
            )
        )
        menu.addItem_(
            self._menu_item(
                "Return home after display change",
                "toggleDisplayChange:",
                self.settings.relocate_after_display_change,
            )
        )
        menu.addItem_(Cocoa.NSMenuItem.separatorItem())
        menu.addItem_(self._menu_item("Quit", "quit:", False))

    def _modifier_menu(self):
        menu = Cocoa.NSMenu.alloc().init()
        for key, title in (
            ("none", "None"),
            ("control", "Control"),
            ("option", "Option"),
            ("command", "Command"),
            ("shift", "Shift"),
        ):
            item = self._menu_item(title, "setModifier:", self.settings.modifier == key)
            item.setRepresentedObject_(key)
            menu.addItem_(item)
        return menu

    def _make_submenu(self, title: str, submenu):
        item = Cocoa.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        item.setSubmenu_(submenu)
        return item

    def _menu_item(self, title: str, action: str, checked: bool):
        item = Cocoa.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, ""
        )
        item.setTarget_(self)
        state = (
            Cocoa.NSControlStateValueOn if checked else Cocoa.NSControlStateValueOff
        )
        item.setState_(state)
        return item

    def toggleLock_(self, _sender) -> None:
        self.settings.lock_enabled = not self.settings.lock_enabled
        self.settings.save()

    def toggleWake_(self, _sender) -> None:
        self.settings.relocate_after_wake = not self.settings.relocate_after_wake
        self.settings.save()

    def toggleDisplayChange_(self, _sender) -> None:
        self.settings.relocate_after_display_change = (
            not self.settings.relocate_after_display_change
        )
        self.settings.save()

    def setModifier_(self, sender) -> None:
        self.settings.modifier = str(sender.representedObject())
        self.settings.save()

    def toggleDisplay_(self, sender) -> None:
        key = str(sender.representedObject())
        keys = list(self.settings.allowed_keys)
        if key in keys:
            keys.remove(key)
        else:
            keys.append(key)
        self.settings.allowed_keys = keys
        self.settings.save()

    def didWake_(self, _notification) -> None:
        if self.settings.relocate_after_wake:
            self._schedule_home()

    def screensChanged_(self, _notification) -> None:
        if self.settings.relocate_after_display_change:
            self._schedule_home()

    def _schedule_home(self) -> None:
        Cocoa.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
            self, "enforceHome:", None
        )
        self.performSelector_withObject_afterDelay_(
            "enforceHome:", None, WAKE_DELAY_SECONDS
        )

    def enforceHome_(self, _ignored) -> None:
        if not self.settings.lock_enabled or self._modifier_held():
            return
        snapshot = capture_displays()
        allowed = self._connected_allowed(snapshot)
        if not allowed:
            return
        dock = locate_dock(snapshot)
        if dock.display_key == allowed[0].key:
            return
        self._start_move(snapshot, allowed[0])

    def tick_(self, _timer) -> None:
        snapshot = capture_displays()
        self._guard_snapshot = snapshot
        if self._moving or not self.settings.lock_enabled or self._modifier_held():
            return
        if time.monotonic() < self._cooldown_until:
            return
        allowed = self._connected_allowed(snapshot)
        if not allowed:
            return
        dock = locate_dock(snapshot)
        if not dock.found or dock.display_key in {item.key for item in allowed}:
            return
        self._start_move(snapshot, allowed[0])

    def _start_move(self, snapshot: DisplaySnapshot, target: DisplayInfo) -> None:
        if self._moving:
            return
        self._moving = True
        self._stop.clear()
        dwell = self.settings.dwell_seconds

        def worker() -> None:
            try:
                moved = move_dock_to_display(snapshot, target, dwell, self._stop)
                _debug(f"move to {target.name}: {moved}")
            finally:
                self._moving = False
                self._cooldown_until = time.monotonic() + RETRY_PAUSE_SECONDS

        threading.Thread(target=worker, name="dock-move", daemon=True).start()

    def quit_(self, _sender) -> None:
        self._stop.set()
        Cocoa.NSApp.terminate_(None)


def main() -> None:
    app = Cocoa.NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(Cocoa.NSApplicationActivationPolicyAccessory)
    # NSApplication does not retain a Python attribute, so keep the delegate here.
    global _DELEGATE
    _DELEGATE = delegate
    AppHelper.runEventLoop()


_DELEGATE = None


if __name__ == "__main__":
    main()
