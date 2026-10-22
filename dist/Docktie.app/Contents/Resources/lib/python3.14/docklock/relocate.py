"""Keep the Dock off displays that are not allowed.

The Dock follows the real cursor. A session-tap mouse event with the
DLOCKMOU user-data tag does not move it. Two observed pieces do:

- while the cursor is inside a display, the original keeps X and replaces Y
- hidePointer / showPointer(CGPoint) plus a CursorPresentation stand in for
  the cursor while a warp runs, then the warp is undone

The bottom strip height is ours: the binary clamps an inset to at least 1
point, and the stored inset was not tied to a named field.
"""

from __future__ import annotations

import threading
import time

import Cocoa
import Quartz
from PyObjCTools import AppHelper

from docklock.dock_detect import (
    DisplayInfo,
    DisplaySnapshot,
    cocoa_point_to_quartz,
    locate_dock,
)

EVENT_SOURCE_STATE = Quartz.kCGEventSourceStateCombinedSessionState
# ASCII "DLOCKMOU", stored as the 64-bit user-data field.
EVENT_USER_DATA = 0x444C4F434B4D4F55
BOTTOM_GUARD_POINTS = 2.0
POLL_SECONDS = 0.05
_DISABLED_TAP_TYPES = {
    int(Quartz.kCGEventTapDisabledByTimeout),
    int(Quartz.kCGEventTapDisabledByUserInput),
}


def _event_source():
    return Quartz.CGEventSourceCreate(EVENT_SOURCE_STATE)


def _current_quartz_point(source) -> tuple[float, float]:
    event = Quartz.CGEventCreate(source)
    point = Quartz.CGEventGetLocation(event)
    return (float(point.x), float(point.y))


def _post_move(source, point: tuple[float, float]) -> None:
    button_down = Quartz.CGEventSourceButtonState(
        EVENT_SOURCE_STATE, Quartz.kCGMouseButtonLeft
    )
    event_type = (
        Quartz.kCGEventLeftMouseDragged if button_down else Quartz.kCGEventMouseMoved
    )
    event = Quartz.CGEventCreateMouseEvent(
        source, event_type, point, Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventSetIntegerValueField(
        event, Quartz.kCGEventSourceUserData, EVENT_USER_DATA
    )
    Quartz.CGEventPost(Quartz.kCGSessionEventTap, event)


def _bottom_center(display: DisplayInfo, arrangement_top: float) -> tuple[float, float]:
    x, y, width, _height = display.frame
    # Two pixels above the bottom edge keeps the point inside the display.
    return cocoa_point_to_quartz(x + (width / 2.0), y + 2.0, arrangement_top)


def lift_quartz_point(
    snapshot: DisplaySnapshot,
    allowed_keys,
    quartz_point: tuple[float, float],
) -> tuple[float, float] | None:
    """Return a point just above the bottom strip, or None when no lift is needed.

    X stays the same. Only a display that is not allowed is guarded, and only
    when the cursor is already inside that display's bottom strip.
    """
    x, quartz_y = quartz_point
    cocoa_y = snapshot.arrangement_top - quartz_y
    allowed = set(allowed_keys)
    for display in snapshot.displays:
        if display.key in allowed:
            continue
        origin_x, origin_y, width, height = display.frame
        if not (origin_x <= x < origin_x + width):
            continue
        if not (origin_y <= cocoa_y < origin_y + height):
            continue
        if cocoa_y >= origin_y + BOTTOM_GUARD_POINTS:
            continue
        lifted_y = origin_y + BOTTOM_GUARD_POINTS
        return (x, snapshot.arrangement_top - lifted_y)
    return None


def guard_cursor(snapshot: DisplaySnapshot, allowed_keys) -> bool:
    """Warp the cursor out of a forbidden bottom strip. Returns True if it did."""
    source = _event_source()
    lifted = lift_quartz_point(
        snapshot, allowed_keys, _current_quartz_point(source)
    )
    if lifted is None:
        return False
    Quartz.CGWarpMouseCursorPosition(lifted)
    return True


def install_bottom_guard(decide):
    """Install the session event tap used to lift the cursor.

    decide() returns (snapshot, allowed_keys) or None to leave the event
    alone. The returned callback and run-loop source must be kept alive.
    The tap itself is None when macOS refuses to create it.
    """
    holder: dict[str, object] = {"tap": None}

    def callback(_proxy, event_type, event, _refcon):
        tap = holder["tap"]
        if int(event_type) in _DISABLED_TAP_TYPES:
            if tap is not None:
                Quartz.CGEventTapEnable(tap, True)
            return event
        try:
            decision = decide()
            if decision is None:
                return event
            snapshot, allowed_keys = decision
            point = Quartz.CGEventGetLocation(event)
            lifted = lift_quartz_point(
                snapshot, allowed_keys, (float(point.x), float(point.y))
            )
            if lifted is None:
                return event
            Quartz.CGWarpMouseCursorPosition(lifted)
            Quartz.CGEventSetLocation(event, lifted)
        except Exception:
            return event
        return event

    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
    )
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault,
        mask,
        callback,
        None,
    )
    holder["tap"] = tap
    source = None
    if tap is not None:
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
    return tap, source, callback


class CursorStandIn:
    """Click-through arrow shown at the real mouse while that mouse is hidden."""

    def __init__(self) -> None:
        self.window = None

    def show(self, cocoa_x: float, cocoa_y: float) -> None:
        cursor = Cocoa.NSCursor.currentSystemCursor()
        if cursor is None:
            cursor = Cocoa.NSCursor.arrowCursor()
        image = cursor.image()
        if image is None:
            return
        hot = cursor.hotSpot()
        size = image.size()
        width = float(size.width)
        height = float(size.height)
        frame = Cocoa.NSMakeRect(
            cocoa_x - float(hot.x),
            cocoa_y - float(hot.y),
            width,
            height,
        )
        window = Cocoa.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            Cocoa.NSWindowStyleMaskBorderless,
            Cocoa.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(Cocoa.NSColor.clearColor())
        window.setLevel_(Cocoa.NSScreenSaverWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setHidesOnDeactivate_(False)
        window.setCollectionBehavior_(
            Cocoa.NSWindowCollectionBehaviorCanJoinAllSpaces
            | Cocoa.NSWindowCollectionBehaviorStationary
            | Cocoa.NSWindowCollectionBehaviorFullScreenAuxiliary
            | Cocoa.NSWindowCollectionBehaviorIgnoresCycle
        )
        view = Cocoa.NSImageView.alloc().initWithFrame_(
            Cocoa.NSMakeRect(0.0, 0.0, width, height)
        )
        view.setImage_(image)
        window.setContentView_(view)
        window.orderFrontRegardless()
        self.window = window

    def close(self) -> None:
        window = self.window
        self.window = None
        if window is not None:
            window.orderOut_(None)


def _on_main(action):
    if threading.current_thread() is threading.main_thread():
        return action()
    done = threading.Event()
    box: dict[str, object] = {}

    def runner() -> None:
        try:
            box["value"] = action()
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    AppHelper.callAfter(runner)
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def move_dock_to_display(
    snapshot: DisplaySnapshot,
    target: DisplayInfo,
    dwell_seconds: float,
    stop_event: threading.Event,
) -> bool:
    """Park a hidden cursor on the target bottom edge, then put it back.

    A still arrow stays at the position the user was at. The real cursor is
    warped once, not walked across the screens, and warped back before it
    is shown again.
    """
    source = _event_source()
    saved = _current_quartz_point(source)
    bottom = _bottom_center(target, snapshot.arrangement_top)
    cocoa_x = saved[0]
    cocoa_y = snapshot.arrangement_top - saved[1]
    stand_in = CursorStandIn()
    hidden = False

    def show() -> None:
        nonlocal hidden
        stand_in.show(cocoa_x, cocoa_y)
        Cocoa.NSCursor.hide()
        hidden = True

    def conceal() -> None:
        stand_in.close()
        if hidden:
            Cocoa.NSCursor.unhide()

    _on_main(show)
    try:
        deadline = time.monotonic() + max(0.0, dwell_seconds)
        while time.monotonic() < deadline:
            if stop_event.is_set():
                return False
            Quartz.CGWarpMouseCursorPosition(bottom)
            _post_move(source, bottom)
            if locate_dock(snapshot).display_key == target.key:
                return True
            time.sleep(POLL_SECONDS)
        return locate_dock(snapshot).display_key == target.key
    finally:
        Quartz.CGWarpMouseCursorPosition(saved)
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)
        _on_main(conceal)
