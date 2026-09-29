"""A private X server and fake emulator windows, for testing wslg-toolbar.py for real.

XServer starts Xvfb (a virtual X server with no screen) on a free display from :99 up. $SCRIPT_TESTS_DISPLAY
uses an existing display instead, e.g. ":0" to watch the windows appear under WSLg.
EmulatorWindows recreates the window structure the Android Emulator shows (see the docstring of
wslg-toolbar.py) through Xlib, and reads back what the script did to it. On a display with a window
manager (WSLg's), mapping a window only asks the window manager to map it, which it does a little
later, so EmulatorWindows waits for it (Xvfb has no window manager and maps windows at once).
WindowChurn is another client that keeps opening and closing windows, as a desktop's tooltips and
menus do, while the script searches the display.
"""
import contextlib
import ctypes
import os
import shutil
import subprocess
import sys
import time

# Xlib constants (X11/X.h, X11/Xutil.h, X11/Xatom.h).
IS_UNMAPPED = 0
WINDOW_GROUP_HINT = 1 << 6
XA_ATOM = 4
ANY_PROPERTY_TYPE = 0


class XWMHints(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_long), ("input", ctypes.c_int), ("initial_state", ctypes.c_int),
                ("icon_pixmap", ctypes.c_ulong), ("icon_window", ctypes.c_ulong),
                ("icon_x", ctypes.c_int), ("icon_y", ctypes.c_int),
                ("icon_mask", ctypes.c_ulong), ("window_group", ctypes.c_ulong)]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
                ("height", ctypes.c_int), ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
                ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("class_", ctypes.c_int),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
                ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


def unavailable_reason():
    """Why the X tests can't run here, or None if they can."""
    if os.environ.get("SCRIPT_TESTS_DISPLAY"):
        return None
    if not shutil.which("Xvfb"):
        return "Xvfb isn't installed (apt install xvfb / dnf install xorg-x11-server-Xvfb)"
    try:
        ctypes.cdll.LoadLibrary("libX11.so.6")
    except OSError:
        return "libX11 isn't installed"
    return None


class XServer:
    """Context manager: yields the DISPLAY name of a private Xvfb (or $SCRIPT_TESTS_DISPLAY)."""

    def __enter__(self):
        self.process = None
        if os.environ.get("SCRIPT_TESTS_DISPLAY"):
            return os.environ["SCRIPT_TESTS_DISPLAY"]
        # Only an abstract socket (-nolisten unix): WSLg mounts /tmp/.X11-unix read-only, so no
        # socket file can be created there. The display number is chosen explicitly, far from the
        # real ones: Xvfb's own choice can be :0, and clients try the abstract socket first, so the
        # desktop's apps would connect to this server instead.
        for number in range(99, 150):
            read, write = os.pipe()
            # -displayfd: Xvfb writes the display number to the pipe once it accepts connections,
            # or closes it without writing if the number is taken. -noreset: by default the server
            # resets when its last client disconnects, which is between two tests (one test's
            # EmulatorWindows closes, the next one's opens), and a connection made while it
            # resets fails with "cannot open display", more often the busier the machine.
            process = subprocess.Popen(
                ["Xvfb", f":{number}", "-displayfd", str(write), "-nolisten", "tcp",
                 "-nolisten", "unix", "-noreset"], pass_fds=[write], stderr=subprocess.DEVNULL)
            os.close(write)
            with os.fdopen(read) as pipe:
                ready = pipe.readline().strip()
            if ready:
                self.process = process
                return f":{number}"
            process.wait()
        raise RuntimeError("Xvfb didn't start on any display from :99 to :149")

    def __exit__(self, *exc):
        if self.process:
            self.process.terminate()
            self.process.wait()


class EmulatorWindows:
    """Opens an X connection and creates emulator windows on it; closing it destroys them."""

    def __init__(self, display):
        self.x = ctypes.cdll.LoadLibrary("libX11.so.6")
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XInternAtom.restype = ctypes.c_ulong
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XCreateSimpleWindow.restype = ctypes.c_ulong
        self.x.XGetAtomName.restype = ctypes.c_void_p
        self.x.XGetWMHints.restype = ctypes.POINTER(XWMHints)
        self.d = ctypes.c_void_p(self.x.XOpenDisplay(display.encode()))
        if not self.d.value:
            raise RuntimeError(f"cannot open display {display}")
        self.root = ctypes.c_ulong(self.x.XDefaultRootWindow(self.d))

    def close(self):
        self.x.XCloseDisplay(self.d)

    # --- Building ---------------------------------------------------------------------------

    def emulator(self, avd, port=5554):
        """Creates one emulator's windows as the real emulator does; returns (main, toolbar, bar).
        The bar is the 620x21 window titled "Emulator" like the toolbar, hidden as it is once the
        emulator has booted. It's created after the toolbar, so it's above it, as on WSLg after
        boot; raise_() puts the toolbar on top, as a window manager does when it maps it."""
        leader = self._window("Emulator", 1, 1, mapped=False)       # hidden group leader
        self._set_group(leader, leader)
        main = self._window(f"Android Emulator - {avd}:{port}", 400, 225)
        self._set_group(main, leader)
        toolbar = self.utility(main, 54, 418)
        bar = self.utility(main, 620, 21, mapped=False)
        extended = self._window("Extended Controls", 300, 300)
        self._set_group(extended, leader)
        if not (self.wait_until_mapped(main) and self.wait_until_mapped(toolbar)):
            raise RuntimeError(f"the window manager didn't map the windows of {avd}")
        return main, toolbar, bar

    def utility(self, main, width, height, mapped=True):
        """Another window titled "Emulator" in main's group, a transient utility of it, like the
        toolbar and the bar."""
        hints = self.x.XGetWMHints(self.d, ctypes.c_ulong(main))
        leader = hints.contents.window_group
        self.x.XFree(hints)
        win = self._window("Emulator", width, height, mapped=False)  # mapped once it's transient
        self._set_group(win, leader)
        self.x.XSetTransientForHint(self.d, ctypes.c_ulong(win), ctypes.c_ulong(main))
        if mapped:
            self.x.XMapWindow(self.d, ctypes.c_ulong(win))
        self.x.XSync(self.d, 0)
        return win

    def raise_(self, win):
        self.x.XRaiseWindow(self.d, ctypes.c_ulong(win))
        self.x.XSync(self.d, 0)

    def _window(self, title, width, height, mapped=True):
        win = self.x.XCreateSimpleWindow(self.d, self.root, 0, 0, width, height, 0, 0, 0)
        self.x.XStoreName(self.d, ctypes.c_ulong(win), title.encode())
        if mapped:
            self.x.XMapWindow(self.d, ctypes.c_ulong(win))
        return win

    def destroy(self, win):
        self.x.XDestroyWindow(self.d, ctypes.c_ulong(win))
        self.x.XSync(self.d, 0)

    @contextlib.contextmanager
    def grabbed(self):
        """While the block runs, the X server handles only this connection's requests: other
        clients, such as the script, wait."""
        self.x.XGrabServer(self.d)
        self.x.XSync(self.d, 0)
        try:
            yield
        finally:
            self.x.XUngrabServer(self.d)
            self.x.XSync(self.d, 0)

    def _set_group(self, win, leader):
        hints = XWMHints(flags=WINDOW_GROUP_HINT, window_group=leader)
        self.x.XSetWMHints(self.d, ctypes.c_ulong(win), ctypes.byref(hints))

    # --- Reading back -----------------------------------------------------------------------

    def is_mapped(self, win):
        self.x.XSync(self.d, 0)
        attrs = XWindowAttributes()
        self.x.XGetWindowAttributes(self.d, ctypes.c_ulong(win), ctypes.byref(attrs))
        return attrs.map_state != IS_UNMAPPED

    def wait_until_mapped(self, win, timeout=10, mapped=True):
        """Whether win is mapped (or unmapped, with mapped=False) once it is, or after timeout
        seconds: a window manager maps a window some time after its client asked for it."""
        deadline = time.monotonic() + timeout
        while self.is_mapped(win) != mapped and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.is_mapped(win) == mapped

    def transient_for(self, win):
        parent = ctypes.c_ulong()
        found = self.x.XGetTransientForHint(self.d, ctypes.c_ulong(win), ctypes.byref(parent))
        return parent.value if found else None

    def window_types(self, win):
        """Names of the atoms in _NET_WM_WINDOW_TYPE."""
        actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
        count, after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ulong)()
        atom = self.x.XInternAtom(self.d, b"_NET_WM_WINDOW_TYPE", 0)
        self.x.XGetWindowProperty(self.d, ctypes.c_ulong(win), ctypes.c_ulong(atom), 0, 16, 0,
                                  ctypes.c_ulong(XA_ATOM), ctypes.byref(actual_type),
                                  ctypes.byref(fmt), ctypes.byref(count), ctypes.byref(after),
                                  ctypes.byref(data))
        names = []
        for i in range(count.value):
            name = self.x.XGetAtomName(self.d, ctypes.c_ulong(data[i]))
            names.append(ctypes.string_at(name).decode())
            self.x.XFree(ctypes.c_void_p(name))
        if data:
            self.x.XFree(data)
        return names


class WindowChurn:
    """Context manager: while it's open, another client keeps a few hundred windows on the display
    and replaces them all the time, the oldest first, so that windows the script has just listed
    close before it reads them. Runs this file as a separate process, with its own X connection,
    and returns once every window of the first set has been replaced."""

    def __init__(self, display):
        self.display = display

    def __enter__(self):
        self.process = subprocess.Popen([sys.executable, __file__, "churn", self.display],
                                        stdout=subprocess.PIPE, text=True)
        if not self.process.stdout.readline():
            raise RuntimeError(f"the window churn didn't start on {self.display}")
        return self

    def __exit__(self, *exc):
        self.process.terminate()
        self.process.wait()
        self.process.stdout.close()


def churn(display, count=500):
    # XQueryTree lists a window's children from the bottom of the stack up, and a new window
    # goes on top, so the script reads these windows oldest first: the ones replaced next.
    windows = EmulatorWindows(display)
    x, d, root = windows.x, windows.d, windows.root
    alive = [x.XCreateSimpleWindow(d, root, 0, 0, 1, 1, 0, 0, 0) for _ in range(count)]
    replaced = 0
    while True:
        x.XDestroyWindow(d, ctypes.c_ulong(alive.pop(0)))
        alive.append(x.XCreateSimpleWindow(d, root, 0, 0, 1, 1, 0, 0, 0))
        replaced += 1
        if replaced == count:
            x.XSync(d, 0)
            print("churning", flush=True)


if __name__ == "__main__" and sys.argv[1:2] == ["churn"]:
    churn(sys.argv[2])
