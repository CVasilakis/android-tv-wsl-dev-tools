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
- In "show", waiting after the unmap until the window manager has withdrawn the toolbar (its
  WM_STATE Withdrawn): the window manager handles the unmap before the toolbar is changed and
  mapped again, so the map is a fresh one. On an idle host WSLg's window manager has withdrawn
  it by the script's first look, and mapping it at once worked too, but then it had been
  withdrawn before the map anyway; a host short of CPU can delay the window manager by any
  amount. Without WM_STATE no window manager manages the toolbar (Xvfb), and Withdrawn it was
  hidden already: nothing to wait for. A window manager that never withdraws it gets the map
  after WSLG_TOOLBAR_TIMEOUT anyway, with a warning: it receives the unmap first in any case.
  Re-mapping the toolbar of a running emulator, with its original properties put back, doesn't
  park it at (-32768, -32768) again, so a change here can only be checked on a freshly started
  emulator (EMULATOR_TOOLBAR=show start-emulator.sh), with a real mouse.
- In "show", moving the toolbar again once the window manager has mapped it (WM_STATE Normal)
  and then moved it: WSLg's window manager ignores the position asked before the map. A few
  milliseconds after marking the toolbar Normal it places it itself: over the emulator at the
  top of the screen the first time after boot, later 32 px up and left of where it was (its
  frame's invisible margin). A move made before that is undone, which left the toolbar at the
  top of the screen after a boot. So the script waits until the toolbar has left the position
  it had at the map, for up to PLACE_TIMEOUT, and then moves it where the emulator itself does
  when its window is moved: the toolbar's frame against the right of the main window's frame
  (read from _NET_FRAME_EXTENTS, which includes WSLg's invisible margins), its top level with
  the top of the emulator's screen. Moving the emulator window later makes the emulator place
  the toolbar there again.
- Reading _NET_WM_NAME before WM_NAME: Qt can leave the toolbar's WM_NAME empty (emulator
  37.1 does from the start), and only _NET_WM_NAME still says "Emulator".
- Skipping the group leader: a hidden 1x1 window is also titled "Emulator".
- Picking the window taller than wide: the 620x21 bar (see "The emulator's windows") has
  everything else in common with the toolbar. The first "Emulator" window found isn't
  enough: the window manager puts a window it maps on top, so after "show" the bar came first
  and a "hide" then unmapped the bar, which was hidden anyway, and left the toolbar shown.
  When several windows are taller than wide, the script looks again until its time limit and
  then fails rather than guess.
- The X error handler ignoring BadWindow during the search: the search reads every window on
  the display, and any of them (a tooltip, a menu, the emulator's own startup windows) can
  close between being listed and being read. Xlib's default handler ends the program on that
  error, and start-emulator.sh ignores this script's failure, so the toolbar would stay shown.
  Once the toolbar is found, every X error is fatal again, as it should be for the windows the
  script changes.

Things that don't work (don't try them again)
---------------------------------------------
- XMoveWindow on the toolbar or its frame: WSLg's window manager moves it straight back to
  (-32768, -32768) while it stays a transient utility window.
- Setting WM_HINTS input=False / dropping WM_TAKE_FOCUS: the toolbar still takes keyboard focus.
- Setting the toolbar's position before mapping it, with XMoveWindow or with USPosition and
  PPosition in WM_NORMAL_HINTS: WSLg's window manager places it where it chooses.
- Raising or activating the emulator window (XRaiseWindow, a _NET_ACTIVE_WINDOW request), to
  bring it in front of Windows apps after boot: WSLg ignores both.
- Simulated input (XTest) and XSetInputFocus, e.g. for automated tests: XWayland under WSLg
  ignores them, so only real mouse and keyboard input can test this.

The emulator's windows
----------------------
Seen with emulator 37.1 under WSLg (tv_api25, booted -read-only). All share
one window group (WM_HINTS) and WM_CLIENT_LEADER; all but the leader have WM_CLASS
"qemu-system-<arch>", "Emulator".

  "Android Emulator - <avd>:<port>"  the screen (960x540 for tv_api25); a normal, mapped
                                     window
  "Emulator" 54x418 (the toolbar)    fixed size (WM_NORMAL_HINTS min = max); WM_TRANSIENT_FOR
                                     the main window, _NET_WM_WINDOW_TYPE UTILITY (plus KDE
                                     OVERRIDE and NORMAL); mapped by the emulator, its frame
                                     parked at (-32768, -32768)
  "Emulator" 620x21 (a bar)          purpose unknown (a startup message?); the same transient
                                     hint, types, WM_CLASS and _MOTIF_WM_HINTS as the toolbar;
                                     the emulator itself maps it at startup, 620x102 and parked
                                     like the toolbar, and a few seconds after Android has
                                     booted (after start-emulator.sh's "hide") unmaps it
                                     (WM_STATE Withdrawn) and shrinks it to 620x21
                                     (WM_NORMAL_HINTS height min = max = 21)
  "Emulator" 1x1                     the group leader; never mapped, no WM_CLASS
  "Extended Controls"                when opened from the toolbar

Only the toolbar and the bar have their title in _NET_WM_NAME alone. The window manager
reparents the toolbar and the bar into frames, and stacks a window it maps on top of the
others, so their order changes with every "show".

Debugging
---------
  xwininfo -root -tree | grep qemu-system   # windows and absolute positions (x11-utils package)
  xprop -id <window-id>                     # its title, WM_TRANSIENT_FOR, type, group leader
Broken state: the 54x418 "Emulator" window at "+-32736+-32736". Hidden: `xwininfo -id <id>`
shows "Map State: IsUnMapped". Emulator restarts create new windows, so this script finds them
by title, window group and shape, never by window id.

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
# Seconds to wait for the windows to appear, as they can lag a little behind boot, and in "show"
# for the window manager to withdraw the toolbar. The override is for the tests (tests/), which
# don't want to wait 30 s for "not found".
TIMEOUT = float(os.environ.get("WSLG_TOOLBAR_TIMEOUT", "30"))

# Seconds to wait in "show" for WSLg's window manager to place the toolbar it has mapped, which it
# does within milliseconds; a window manager that leaves it where it is costs this much.
PLACE_TIMEOUT = 2

# Xlib constants used below (values from X11/X.h and X11/Xutil.h).
XA_ATOM = 4                # property type "ATOM"
XA_CARDINAL = 6            # property type "CARDINAL"
WITHDRAWN, NORMAL = 0, 1   # WM_STATE's states (ICCCM 4.1.3.1)
BAD_WINDOW = 3             # error code
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


class XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


x11.XGetWMHints.restype = ctypes.POINTER(XWMHints)
ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
x11.XSetErrorHandler.argtypes = [ERROR_HANDLER]
x11.XSetErrorHandler.restype = ERROR_HANDLER

searching = False  # True while find_windows() reads windows that may close at any moment


def on_x_error(display, event):
    # The search only makes calls that wait for the server's answer (XQueryTree,
    # XGetWindowProperty, XFetchName, XGetWMHints, XGetWindowAttributes), so an error arrives
    # during the call that caused it, and that call then returns "nothing": no children, no
    # name, no hints, no size. The only error a window closing meanwhile causes them is
    # BadWindow. Anything else goes to Xlib's default handler, which prints the error and exits
    # with status 1.
    if searching and event.contents.error_code == BAD_WINDOW:
        return 0
    return xlib_default_error_handler(display, event)


on_x_error = ERROR_HANDLER(on_x_error)  # kept in a variable: Xlib holds only a C pointer to it
xlib_default_error_handler = x11.XSetErrorHandler(on_x_error)

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


def wm_state(win):
    """The state the window manager gave win in its WM_STATE property, or None without one: a
    window no window manager manages."""
    actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
    n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)()
    if x11.XGetWindowProperty(d, ctypes.c_ulong(win), atom("WM_STATE"), 0, 1, 0, atom("WM_STATE"),
                              ctypes.byref(actual_type), ctypes.byref(fmt), ctypes.byref(n),
                              ctypes.byref(after), ctypes.byref(data)) != 0 or not data:
        return None
    state = data[0] if n.value and fmt.value == 32 else None
    x11.XFree(data)
    return state


def frame_extents(win):
    """The width of the window manager's frame around win on its left, right, top and bottom
    (_NET_FRAME_EXTENTS), or zeros without a window manager."""
    actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
    n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)()
    if x11.XGetWindowProperty(d, ctypes.c_ulong(win), atom("_NET_FRAME_EXTENTS"), 0, 4, 0,
                              ctypes.c_ulong(XA_CARDINAL), ctypes.byref(actual_type),
                              ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(after),
                              ctypes.byref(data)) != 0 or not data:
        return 0, 0, 0, 0
    extents = tuple(data[i] for i in range(4)) if n.value == 4 else (0, 0, 0, 0)
    x11.XFree(data)
    return extents


def find_windows():
    global searching
    searching = True
    try:
        return search()
    finally:
        searching = False


def taller_than_wide(win):
    attrs = XWindowAttributes()
    # 0: the window closed since it was listed.
    return bool(x11.XGetWindowAttributes(d, ctypes.c_ulong(win), ctypes.byref(attrs))) \
        and attrs.height > attrs.width


def search():
    windows = list(all_windows(root.value))
    # The main window's title is "Android Emulator - <avd>:<port>".
    prefix = f"Android Emulator - {AVD}:" if AVD else "Android Emulator - "
    mains = [w for w in windows if name(w).startswith(prefix)]
    if not mains:
        return None, []
    if len(mains) > 1 and not AVD:
        titles = ", ".join(name(w).removeprefix("Android Emulator - ") for w in mains)
        sys.exit(f"wslg-toolbar: several emulators are running ({titles}); pass the AVD name")
    main = mains[0]
    group = group_leader(main)
    # The toolbar is titled plain "Emulator" and shares the main window's group. So do the group
    # leader itself (a hidden 1x1 window), skipped, and a 620x21 bar: the toolbar is the
    # one taller than wide, a column of buttons. Their order on the display can't tell them
    # apart: it changes whenever one is mapped. "Extended Controls" windows share the group too,
    # but have a different title.
    return main, [w for w in windows
                  if w not in (main, group) and name(w) == "Emulator" and group_leader(w) == group
                  and taller_than_wide(w)]


deadline = time.monotonic() + TIMEOUT  # monotonic: WSL2 steps the clock after the host sleeps
main, toolbars = find_windows()
while len(toolbars) != 1 and time.monotonic() < deadline:
    time.sleep(1)
    main, toolbars = find_windows()
if len(toolbars) > 1:
    # A new emulator build with another window that looks like the toolbar: changing the wrong
    # one would leave the toolbar as it was, so don't guess.
    ids = ", ".join(hex(w) for w in toolbars)
    sys.exit(f"wslg-toolbar: several windows look like the toolbar ({ids}); "
             "check with `xwininfo -root -tree | grep qemu-system`")
if not toolbars:
    # Wrong AVD name, emulator not running, or a new emulator build that titles or shapes its
    # windows differently: check with `xwininfo -root -tree | grep qemu-system`.
    sys.exit(f"wslg-toolbar: no toolbar window found for AVD '{AVD}'" if AVD
             else "wslg-toolbar: no running emulator window found")

toolbar = toolbars[0]
tb = ctypes.c_ulong(toolbar)
managed = wm_state(toolbar) is not None  # a window manager has had the toolbar (not on Xvfb)
x11.XUnmapWindow(d, tb)  # this alone is the "hide" mode
x11.XSync(d, 0)


def wait_for_wm_state(states, failure):
    """Waits until the toolbar's WM_STATE is one of states; after TIMEOUT, says failure."""
    deadline = time.monotonic() + TIMEOUT
    while wm_state(toolbar) not in states:
        if time.monotonic() >= deadline:
            print(f"wslg-toolbar: the window manager didn't {failure}", file=sys.stderr)
            return False
        time.sleep(0.005)
    return True


def position(win):
    x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x11.XTranslateCoordinates(d, ctypes.c_ulong(win), root, 0, 0,
                              ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
    return x.value, y.value


def beside_main():
    """Where the emulator itself puts its toolbar: the toolbar's frame against the right of the
    main window's frame, its top level with the top of the emulator's screen."""
    attrs = XWindowAttributes()
    x11.XGetWindowAttributes(d, ctypes.c_ulong(main), ctypes.byref(attrs))
    x, y = position(main)
    _, main_right, _, _ = frame_extents(main)
    left, _, top, _ = frame_extents(toolbar)
    return x + attrs.width + main_right + left, y + top


if MODE == "show":
    # Let the window manager withdraw the toolbar before it's changed and mapped again (see
    # docstring). Without WM_STATE no window manager manages it (Xvfb), and Withdrawn it was hidden
    # already: nothing to wait for.
    wait_for_wm_state((None, WITHDRAWN),
                      f"withdraw the toolbar within {TIMEOUT:g} s; showing it anyway")
    # A plain top-level window instead of a transient utility: this is what makes WSLg give it
    # real coordinates, and therefore clickable buttons.
    x11.XDeleteProperty(d, tb, atom("WM_TRANSIENT_FOR"))
    normal = (ctypes.c_ulong * 1)(atom("_NET_WM_WINDOW_TYPE_NORMAL").value)
    x11.XChangeProperty(d, tb, atom("_NET_WM_WINDOW_TYPE"), ctypes.c_ulong(XA_ATOM), 32,
                        PROP_MODE_REPLACE, normal, 1)
    # Without a window manager this move places it. WSLg's places a window it maps where it
    # chooses, a moment after marking it WM_STATE Normal, but follows a move after that (see
    # docstring).
    x11.XMoveWindow(d, tb, *beside_main())
    x11.XSync(d, 0)
    mapped_at = position(toolbar)
    x11.XMapWindow(d, tb)
    x11.XSync(d, 0)
    if managed and wait_for_wm_state((NORMAL,), f"show the toolbar within {TIMEOUT:g} s; "
                                                "it may not be beside the emulator"):
        deadline = time.monotonic() + PLACE_TIMEOUT
        while position(toolbar) == mapped_at and time.monotonic() < deadline:
            time.sleep(0.005)
        x11.XMoveWindow(d, tb, *beside_main())
        x11.XSync(d, 0)

x11.XCloseDisplay(d)
print(f"wslg-toolbar: toolbar {'hidden' if MODE == 'hide' else 'shown as a clickable window'}")
