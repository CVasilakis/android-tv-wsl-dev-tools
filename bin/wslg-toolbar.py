#!/usr/bin/env python3
"""Work around the Android Emulator's side toolbar misbehaving under WSLg.

Usage: wslg-toolbar.py [avd-name] [hide|show]
       wslg-toolbar.py --help
       (start-emulator.sh runs it after boot with "hide", or $EMULATOR_TOOLBAR)
Without an AVD name it acts on the only running emulator window.

Problem
-------
The emulator's side toolbar (power, volume, Back, Home, "..." -> Extended controls) is a
separate frameless X11 window, marked as a transient utility of the main emulator window.
Under WSLg (Weston + XWayland + RDP) this breaks input in two ways:

  1. Clicks are lost. WSLg's window manager draws the toolbar next to the emulator, but parks
     its X11 frame at (-32768, -32768). The X pointer can never reach negative coordinates, so
     the X server never delivers the clicks.
  2. Keyboard focus is stolen. While the toolbar is mapped, keys typed into the emulator window
     go to the toolbar (arrow keys move focus between its buttons) and never reach Android.

Modes
-----
  hide (default)  Unmap the toolbar. Typing in the emulator window reaches Android. The
                  emulator's own shortcuts (Ctrl+H Home, Ctrl+Backspace Back, Ctrl+M Menu)
                  still work with the toolbar hidden.
  show            Re-map it as a plain top-level window. WSLg then gives it real coordinates
                  and its buttons are clickable, but typed keys go to the toolbar (use
                  remote.sh for keys).

You can't have both; the trade-off is documented in bin/README.md.

Things that look removable but aren't
-------------------------------------
- The unmap before changing properties: window managers read a window's type and transient
  hints when it is mapped (ICCCM), so the change has to be followed by a fresh map.
- The 0.5 s sleep after unmapping: gives the window manager time to process the unmap before
  the window is mapped again. Unmap, sleep, change, map is the tested sequence.
- Reading _NET_WM_NAME before WM_NAME: after the toolbar has been re-mapped once, Qt leaves
  WM_NAME empty and only _NET_WM_NAME still says "Emulator".
- Skipping the group leader: a hidden 1x1 window is also titled "Emulator".

Things that don't work (don't try them again)
---------------------------------------------
- XMoveWindow on the toolbar or its frame: WSLg's window manager moves it straight back to
  (-32768, -32768) while it stays a transient utility window.
- Setting WM_HINTS input=False / dropping WM_TAKE_FOCUS: the toolbar still takes keyboard focus.
- Simulated input (XTest) and XSetInputFocus, e.g. for automated tests: XWayland under WSLg
  ignores them, so only real mouse and keyboard input can test this.

Debugging
---------
  xwininfo -root -tree | grep qemu-system   # windows and absolute positions (x11-utils package)
  xprop -id <window-id>                     # its title, WM_TRANSIENT_FOR, type, group leader
Broken state: the 54x418 "Emulator" window at "+-32736+-32736". Hidden: `xwininfo -id <id>`
shows "Map State: IsUnMapped". The main window's title is "Android Emulator - <avd>:<port>".
Emulator restarts create new windows, so this script finds them by title and window group,
never by window id.

Only needs libX11 (called through ctypes, so there are no pip dependencies). Safe to re-run
and to switch modes on a running emulator. Restarting the emulator undoes it.
"""
import ctypes
import os
import sys
import time

USAGE = """Usage: wslg-toolbar.py [avd-name] [hide|show]

WSL only. Hides or shows the Android Emulator's side toolbar, which misbehaves under WSLg:
  hide (default)  keys typed into the emulator window reach Android; no toolbar
  show            the toolbar is a separate, clickable window; typed keys go to the toolbar
                  (use remote.sh for keys)

  avd-name    the emulator to act on (default: the only running emulator)
  -h, --help  show this help

start-emulator.sh runs this with "hide" after boot. Restarting the emulator undoes it.
Details: bin/README.md in android-tv-wsl-dev-tools."""

args = sys.argv[1:]
if any(a in ("-h", "--help") for a in args):
    print(USAGE)
    sys.exit(0)
MODE = args.pop() if args and args[-1] in ("hide", "show") else "hide"
if len(args) > 1 or any(a.startswith("-") for a in args):
    sys.exit(USAGE)
AVD = args[0] if args else None  # None: the only running emulator
# Seconds to wait for the windows to appear; they can lag a little behind boot. The override is
# for the tests (tests/), which don't want to wait 30 s for "not found".
TIMEOUT = float(os.environ.get("WSLG_TOOLBAR_TIMEOUT", "30"))

# Xlib constants used below (values from X11/X.h and X11/Xutil.h).
XA_ATOM = 4                # property type "ATOM"
PROP_MODE_REPLACE = 0
WINDOW_GROUP_HINT = 1 << 6

x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XInternAtom.restype = ctypes.c_ulong
x11.XDefaultRootWindow.restype = ctypes.c_ulong


class XWMHints(ctypes.Structure):
    # Must match Xlib's XWMHints layout exactly: it is read from memory Xlib allocates.
    _fields_ = [("flags", ctypes.c_long), ("input", ctypes.c_int), ("initial_state", ctypes.c_int),
                ("icon_pixmap", ctypes.c_ulong), ("icon_window", ctypes.c_ulong),
                ("icon_x", ctypes.c_int), ("icon_y", ctypes.c_int),
                ("icon_mask", ctypes.c_ulong), ("window_group", ctypes.c_ulong)]


class XWindowAttributes(ctypes.Structure):
    # Only the leading fields are used; _rest pads past the real struct size so Xlib's write
    # into it can't overflow. Don't shrink the padding.
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
                ("height", ctypes.c_int), ("_rest", ctypes.c_byte * 256)]


x11.XGetWMHints.restype = ctypes.POINTER(XWMHints)

display = x11.XOpenDisplay(None)
if not display:
    sys.exit("wslg-toolbar: cannot open X display (is WSLg running? is $DISPLAY set?)")
d = ctypes.c_void_p(display)
root = ctypes.c_ulong(x11.XDefaultRootWindow(d))


def atom(name):
    return ctypes.c_ulong(x11.XInternAtom(d, name.encode(), 0))


def children(win):
    r, p = ctypes.c_ulong(), ctypes.c_ulong()
    kids, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
    if not x11.XQueryTree(d, ctypes.c_ulong(win), ctypes.byref(r), ctypes.byref(p),
                          ctypes.byref(kids), ctypes.byref(n)):
        return []
    result = [kids[i] for i in range(n.value)]
    if kids:
        x11.XFree(kids)
    return result


def all_windows(win):
    # Recursive: client windows sit inside window-manager frame windows, not directly under root.
    for child in children(win):
        yield child
        yield from all_windows(child)


def name(win):
    # Prefer _NET_WM_NAME: once the toolbar has been re-mapped, Qt only keeps its title there.
    actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
    n, after, prop = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ubyte)()
    if x11.XGetWindowProperty(d, ctypes.c_ulong(win), atom("_NET_WM_NAME"), 0, 1024, 0,
                              atom("UTF8_STRING"), ctypes.byref(actual_type), ctypes.byref(fmt),
                              ctypes.byref(n), ctypes.byref(after), ctypes.byref(prop)) == 0 and prop:
        value = ctypes.string_at(prop, n.value).decode(errors="replace")
        x11.XFree(prop)
        if value:
            return value
    out = ctypes.c_char_p()
    if x11.XFetchName(d, ctypes.c_ulong(win), ctypes.byref(out)) and out.value:
        return out.value.decode(errors="replace")
    return ""


def group_leader(win):
    hints = x11.XGetWMHints(d, ctypes.c_ulong(win))
    if not hints:
        return None
    group = hints.contents.window_group if hints.contents.flags & WINDOW_GROUP_HINT else None
    x11.XFree(hints)
    return group


def find_windows():
    windows = list(all_windows(root.value))
    # The main window's title is "Android Emulator - <avd>:<port>".
    prefix = f"Android Emulator - {AVD}:" if AVD else "Android Emulator - "
    mains = [w for w in windows if name(w).startswith(prefix)]
    if not mains:
        return None, None
    if len(mains) > 1 and not AVD:
        titles = ", ".join(name(w).removeprefix("Android Emulator - ") for w in mains)
        sys.exit(f"wslg-toolbar: several emulators are running ({titles}); pass the AVD name")
    main = mains[0]
    group = group_leader(main)
    # The toolbar is titled plain "Emulator" and shares the main window's group; the group
    # leader itself (a hidden 1x1 window) has the same title, so skip it. "Extended Controls"
    # windows share the group too, but have a different title.
    toolbar = next((w for w in windows
                    if w not in (main, group) and name(w) == "Emulator" and group_leader(w) == group),
                   None)
    return main, toolbar


deadline = time.time() + TIMEOUT
main, toolbar = find_windows()
while not toolbar and time.time() < deadline:
    time.sleep(1)
    main, toolbar = find_windows()
if not toolbar:
    # Wrong AVD name, emulator not running, or a new emulator build that titles its windows
    # differently: check with `xwininfo -root -tree | grep qemu-system`.
    sys.exit(f"wslg-toolbar: no toolbar window found for AVD '{AVD}'" if AVD
             else "wslg-toolbar: no running emulator window found")

tb = ctypes.c_ulong(toolbar)
x11.XUnmapWindow(d, tb)  # this alone is the "hide" mode
x11.XSync(d, 0)

if MODE == "show":
    time.sleep(0.5)  # let the window manager process the unmap first (see docstring)
    # A plain top-level window instead of a transient utility: this is what makes WSLg give it
    # real coordinates, and therefore clickable buttons.
    x11.XDeleteProperty(d, tb, atom("WM_TRANSIENT_FOR"))
    normal = (ctypes.c_ulong * 1)(atom("_NET_WM_WINDOW_TYPE_NORMAL").value)
    x11.XChangeProperty(d, tb, atom("_NET_WM_WINDOW_TYPE"), ctypes.c_ulong(XA_ATOM), 32,
                        PROP_MODE_REPLACE, normal, 1)

    # Ask for a spot right of the main window. WSLg may choose its own position (typically
    # near the top of the screen), which is fine as long as it's on-screen.
    attrs = XWindowAttributes()
    x11.XGetWindowAttributes(d, ctypes.c_ulong(main), ctypes.byref(attrs))
    mx, my, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x11.XTranslateCoordinates(d, ctypes.c_ulong(main), root, 0, 0,
                              ctypes.byref(mx), ctypes.byref(my), ctypes.byref(child))
    x11.XMoveWindow(d, tb, mx.value + attrs.width + 8, my.value)
    x11.XMapWindow(d, tb)
    x11.XSync(d, 0)

x11.XCloseDisplay(d)
print(f"wslg-toolbar: toolbar {'hidden' if MODE == 'hide' else 'shown as a clickable window'}")
