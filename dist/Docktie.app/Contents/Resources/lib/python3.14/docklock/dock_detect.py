"""Find which display currently holds the Dock.

The shipped DockLock Lite binary selects the first window whose owner name is
"Dock", whose layer equals CGWindowLevelForKey(kCGDockWindowLevelKey), and
whose bounds have a positive width and height. On this Mac that window covers
the whole display the Dock is attached to, rather than the icon strip.
"""

from __future__ import annotations

from dataclasses import dataclass

import Quartz


DOCK_OWNER_NAME = "Dock"


@dataclass(frozen=True)
class DisplayInfo:
    key: str
    name: str
    screen_number: int
    frame: tuple[float, float, float, float]


@dataclass(frozen=True)
class DisplaySnapshot:
    displays: list[DisplayInfo]
    arrangement_top: float


@dataclass(frozen=True)
class DockLocation:
    found: bool
    display_key: str | None
    cocoa_bounds: tuple[float, float, float, float] | None


def display_key(screen_number: int) -> str:
    vendor = int(Quartz.CGDisplayVendorNumber(screen_number))
    model = int(Quartz.CGDisplayModelNumber(screen_number))
    serial = int(Quartz.CGDisplaySerialNumber(screen_number))
    return f"{vendor:08x}-{model:08x}-{serial:08x}"


def capture_displays() -> DisplaySnapshot:
    """Read AppKit screens. Call this on the main thread."""
    import Cocoa

    displays: list[DisplayInfo] = []
    top = 0.0
    for screen in Cocoa.NSScreen.screens():
        frame = screen.frame()
        origin = frame.origin
        size = frame.size
        rect = (float(origin.x), float(origin.y), float(size.width), float(size.height))
        top = max(top, rect[1] + rect[3])
        number = int(screen.deviceDescription()["NSScreenNumber"])
        name = screen.localizedName()
        displays.append(
            DisplayInfo(
                key=display_key(number),
                name=str(name),
                screen_number=number,
                frame=rect,
            )
        )
    return DisplaySnapshot(displays=displays, arrangement_top=top)


def quartz_rect_to_cocoa(
    bounds, arrangement_top: float
) -> tuple[float, float, float, float]:
    """Convert a window-list rect into AppKit screen space.

    Live Dock and wallpaper windows report Y downward from the top of the
    display arrangement. NSScreen frames use Y upward from the bottom-left
    of the main display. The binary flips Y when it matches a window to a
    display; this is that conversion, checked against both attached screens.
    """
    x = float(bounds["X"])
    y = float(bounds["Y"])
    width = float(bounds["Width"])
    height = float(bounds["Height"])
    cocoa_y = arrangement_top - (y + height)
    return (x, cocoa_y, width, height)


def cocoa_point_to_quartz(
    x: float, y: float, arrangement_top: float
) -> tuple[float, float]:
    """Convert an AppKit point into the space used by CGEvent and CGWarp."""
    return (x, arrangement_top - y)


def _contains(frame: tuple[float, float, float, float], x: float, y: float) -> bool:
    origin_x, origin_y, width, height = frame
    return origin_x <= x < origin_x + width and origin_y <= y < origin_y + height


def _display_for_cocoa_rect(
    cocoa_bounds: tuple[float, float, float, float], displays: list[DisplayInfo]
) -> DisplayInfo | None:
    x, y, width, height = cocoa_bounds
    mid_x = x + (width / 2.0)
    mid_y = y + (height / 2.0)
    for display in displays:
        if _contains(display.frame, mid_x, mid_y):
            return display
    return None


def locate_dock(snapshot: DisplaySnapshot) -> DockLocation:
    """Return the display that contains the Dock window midpoint."""
    dock_level = int(Quartz.CGWindowLevelForKey(Quartz.kCGDockWindowLevelKey))
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    )
    for window in windows:
        if str(window.get(Quartz.kCGWindowOwnerName, "")) != DOCK_OWNER_NAME:
            continue
        layer = window.get(Quartz.kCGWindowLayer)
        if layer is None or int(layer) != dock_level:
            continue
        bounds = window.get(Quartz.kCGWindowBounds)
        if bounds is None:
            continue
        width = float(bounds["Width"])
        height = float(bounds["Height"])
        if width <= 0.0 or height <= 0.0:
            continue
        cocoa_bounds = quartz_rect_to_cocoa(bounds, snapshot.arrangement_top)
        display = _display_for_cocoa_rect(cocoa_bounds, snapshot.displays)
        return DockLocation(
            found=display is not None,
            display_key=None if display is None else display.key,
            cocoa_bounds=cocoa_bounds,
        )
    return DockLocation(found=False, display_key=None, cocoa_bounds=None)
