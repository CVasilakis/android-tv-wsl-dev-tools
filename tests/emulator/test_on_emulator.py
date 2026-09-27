"""The scripts against the real SDK and emulator: slow (a boot), so only with run.py --emulator.

Uses the developer's own setup (SDK, AVD) exactly as the scripts find it; set ADT_AVD to pick
the AVD. If that AVD is already running it's reused and left running; otherwise it's booted
without a window and stopped afterwards (without saving a snapshot, so the next normal start is
unaffected). The stop-emulator.sh tests need an AVD of their own to stop, so they skip if the
AVD was already running.
"""
import os
import re
import signal
import subprocess
import time
import unittest
from collections import Counter

from support.sandbox import BIN, LIB

ENABLED = os.environ.get("ADT_EMULATOR_TESTS") == "1"
QUIET_MS = 1000

# Keyboard input to remote.sh -> Android KeyEvent keyCode that must arrive on the device.
KEYS = [("\x1b[A", 19), ("\x1b[B", 20), ("\x1b[D", 21), ("\x1b[C", 22), ("\n", 23),
        ("\x7f", 4), ("m", 82), ("p", 85), ("+", 24), ("-", 25), ("h", 3)]


def lib(snippet):
    """Runs a bash snippet with lib.sh sourced, in the developer's real environment."""
    return subprocess.run(["bash", "-c", f"source {LIB}\n{snippet}"],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def default_avd():
    return os.environ.get("ADT_AVD") or lib(
        'avds="$(list_avds)"; grep -qx tv_api25 <<<"$avds" && echo tv_api25 || head -n1 <<<"$avds"')


def serial_of(avd):
    """The serial of that AVD's running emulator, or None."""
    return lib(f'for s in $(running_emulators); do [ "$(emulator_avd "$s")" = {avd} ] '
               '&& echo "$s"; done') or None


def exited(pid):
    """Whether a process has exited: it's gone, or a zombie its parent hasn't reaped yet, which
    holds nothing (under WSL the emulator's parent is init's relay, which reaps it a moment
    later)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def start(avd, *flags):
    """Boots the AVD the way this tier does: no window, no snapshot saved when it's stopped."""
    return subprocess.run([str(BIN / "start-emulator.sh"), avd, "-no-window", "-no-snapshot-save",
                           *flags], capture_output=True, text=True, timeout=600)


def emulator_pids(*args):
    """PIDs of the processes whose command line holds all these arguments, read from /proc (a
    search that can't find itself, unlike pgrep -f)."""
    pids = []
    for entry in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = f.read().decode(errors="replace").split("\0")
        except (OSError, ValueError):
            continue
        if all(arg in argv for arg in args):
            pids.append(int(entry))
    return pids


class RecentInput:
    """The events Android's input dispatcher handles from now on, from repeated looks at its
    recent queue (`dumpsys input`). The queue keeps only the last 10 events, window focus changes
    included, and Android holds keys for up to 5 s while a window it's opening starts. A key that
    opens windows (OK on Google TV's profile or sign-in screen) brings several focus changes, so
    keys sent right before it can be pushed out before a look sees them. So each look is merged
    into what the ones before it saw, and tests look again after every key or long press: between
    two looks, only one key's worth of events has to fit.

    The queue is first in, first out: a look is the one before it without its oldest events, and
    with the new ones after. Where they overlap, each event is of the same kind, and each one's
    age has grown by the same amount, the time between the looks; that tells how many events
    dropped out. The device's clock, read around each look, only picks between two overlaps that
    both fit (events evenly spaced), and places a look that shares no event with the one before."""

    def __init__(self, adb_shell):
        self.adb_shell = adb_shell
        # (ms since the first look, event without its age), in the order Android handled them,
        # which is the queue's: a key's release can have the time of its press (input keyevent
        # --longpress from API 30 on), and a key held for a new window comes after later events.
        self.seen = []
        self.last = []          # the latest look: (age, event), oldest first
        self.last_time = None   # the latest look's moment, in ms since the first look
        self.first_uptime = None
        self.look()
        self.before = Counter(self.seen)

    def look(self):
        """Takes one more look at the queue, adding the events no look showed before."""
        out = self.adb_shell("cat /proc/uptime; dumpsys input; cat /proc/uptime")
        lines = out.strip().splitlines()
        uptime = (float(lines[0].split()[0]) + float(lines[-1].split()[0])) * 500   # ms, mid-look
        queue = out.split("RecentQueue", 1)[1].split("PendingEvent", 1)[0]
        events = [(float(age.group(1)), re.sub(r",? ?age=[\d.]+ms", "", line.strip()))
                  for line in queue.splitlines() if (age := re.search(r"age=([\d.]+)ms", line))]
        if self.first_uptime is None:
            self.first_uptime = uptime
        estimate = uptime - self.first_uptime
        # (events of the last look that dropped out, time between the looks) of each overlap
        # that fits; the largest overlap, then the time closest to the clock's.
        fits = []
        for dropped in range(len(self.last)):
            overlap = list(zip(self.last[dropped:], events))
            if len(overlap) < len(self.last) - dropped:
                continue   # this look is shorter than the rest of the last one
            gaps = [new_age - old_age for (old_age, old), (new_age, new) in overlap if old == new]
            if len(gaps) == len(overlap) and max(gaps) - min(gaps) <= 1.5 and min(gaps) >= -1.5:
                fits.append((dropped, self.last_time + sum(gaps) / len(gaps)))
        if fits:
            dropped, now = min(fits, key=lambda fit: (fit[0], abs(fit[1] - estimate)))
            new = events[len(self.last) - dropped:]
        else:
            now, new = estimate, events   # no event in common: all of them are new
        self.seen += [(round(now - age, 1), event) for age, event in new]
        self.last, self.last_time = events, now
        return self

    def key_events(self):
        """(ms since the first look, event) of the key events since then, in order. Android
        5-8 print a key-down as action=0, 9 as action=DOWN, and 10 and newer print no details
        ("KeyEvent")."""
        before = Counter(self.before)
        events = []
        for event in self.seen:
            if before[event]:
                before[event] -= 1
            elif event[1].startswith("KeyEvent"):
                events.append(event)
        return events

    def key_downs(self):
        """keyCodes of the key-downs since the first look, oldest first; where Android prints no
        details, one None per press (a down and an up)."""
        events = [event for _, event in self.key_events()]
        if events and "keyCode=" not in events[0]:
            return [None] * (len(events) // 2)
        return [int(re.search(r"keyCode=(\d+)", e).group(1)) for e in events
                if re.search(r"action=(?:0|DOWN),", e)]


@unittest.skipUnless(ENABLED, "real-emulator tests run only with: tests/run.py --emulator")
class OnTheRealEmulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adb = lib('echo "$ADB"')
        cls.avd = default_avd()
        cls.serial = serial_of(cls.avd)
        cls.started_here = cls.serial is None
        if cls.started_here:
            cls.start_result = start(cls.avd)
            if cls.start_result.returncode != 0:
                raise RuntimeError(f"start-emulator.sh failed:\n{cls.start_result.stdout}"
                                   f"{cls.start_result.stderr}")
            cls.serial = serial_of(cls.avd)

    @classmethod
    def tearDownClass(cls):
        # stop-emulator.sh waits until it has exited: the next test class may boot the same AVD.
        if cls.started_here and cls.serial:
            subprocess.run([str(BIN / "stop-emulator.sh"), cls.serial], capture_output=True,
                           timeout=120)

    def adb_shell(self, *args):
        return subprocess.run([self.adb, "-s", self.serial, "shell", *args], capture_output=True,
                              text=True, timeout=60).stdout

    def wait_until_input_is_quiet(self):
        """Waits until the input dispatcher's newest event, of any kind, is QUIET_MS old: no key
        from before, and no focus change of a window a test before opened, can then be taken for
        one that comes after, or push it out of the recent queue (see RecentInput)."""
        deadline = time.time() + 10
        while any(float(age) < QUIET_MS for age in re.findall(
                r"age=([\d.]+)ms", self.adb_shell("dumpsys", "input").split("RecentQueue", 1)[1]
                .split("PendingEvent", 1)[0])):
            self.assertLess(time.time(), deadline, "input events keep coming")
            time.sleep(0.2)

    def test_start_returns_once_android_has_booted(self):
        if not self.started_here:
            self.skipTest(f"{self.avd} was already running")
        self.assertEqual(self.adb_shell("getprop", "sys.boot_completed").strip(), "1")
        self.assertEqual(self.start_result.stdout, f"{self.serial}\n", "stdout is only the serial")
        self.assertIn(f"booted as {self.serial}", self.start_result.stderr)

    def test_start_again_reports_the_running_emulator(self):
        result = subprocess.run([str(BIN / "start-emulator.sh"), self.avd],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{self.serial}\n")
        self.assertIn(f"already running as {self.serial}", result.stderr)

    def focused_package(self):
        """Package of the window in front, from `dumpsys window` (e.g. com.android.tv.settings)."""
        focus = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", self.adb_shell("dumpsys", "window"))
        return focus.group(1) if focus else None

    def test_remote_home_key_leaves_an_app(self):
        # start-emulator.sh first: on API 26 and 27 it's what makes Home work (see its header),
        # and an emulator that was already running may not have been started by it.
        subprocess.run([str(BIN / "start-emulator.sh"), self.avd], capture_output=True, timeout=60)
        # Right after a boot, the home app may still be starting and come to the front over
        # Settings (Google TV), so Settings is opened again until it stays there.
        deadline = time.time() + 30
        while "settings" not in (self.focused_package() or "") and time.time() < deadline:
            self.adb_shell("am", "start", "-W", "-a", "android.settings.SETTINGS")
            time.sleep(2)
        settings = self.focused_package()
        self.assertIn("settings", settings or "", "Settings didn't open")
        result = subprocess.run([str(BIN / "remote.sh"), self.serial], input="hq",
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.time() + 10
        while self.focused_package() == settings and time.time() < deadline:
            time.sleep(0.2)
        self.assertNotEqual(self.focused_package(), settings, "Home didn't leave Settings")

    def test_remote_keys_arrive_as_the_right_android_keys(self):
        # One key at a time, typed into one remote.sh as a person would, looking at the recent
        # queue until the key is there before typing the next: see RecentInput.
        details = int(self.adb_shell("getprop", "ro.build.version.sdk")) < 29
        self.wait_until_input_is_quiet()
        received = RecentInput(self.adb_shell)
        remote = subprocess.Popen([str(BIN / "remote.sh"), self.serial], stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(remote.kill)
        expected = []
        for key, code in KEYS:
            remote.stdin.write(key)
            remote.stdin.flush()
            # Where Android prints no key codes, only the number of presses can be checked.
            expected.append(code if details else None)
            deadline = time.time() + 12   # longer than Android holds keys for a new window
            while len(received.look().key_downs()) < len(expected) and time.time() < deadline:
                time.sleep(0.2)
            self.assertEqual(received.key_downs(), expected, f"after the key for {code}")
        _, err = remote.communicate("q", timeout=30)
        self.assertEqual(remote.returncode, 0, err)

    def test_remote_long_press_holds_the_key(self):
        # DPAD_UP: holding it only moves the focus, whatever is on the screen.
        timeout = self.adb_shell("settings", "get", "secure", "long_press_timeout").strip()
        timeout = int(timeout) if timeout.isdigit() else 500
        rotation = [self.adb_shell("settings", "get", "system", name).strip()
                    for name in ("accelerometer_rotation", "user_rotation")]
        self.wait_until_input_is_quiet()
        received = RecentInput(self.adb_shell)
        remote = subprocess.Popen([str(BIN / "remote.sh"), "--long-press", "DPAD_UP", self.serial],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(remote.kill)
        while remote.poll() is None:   # looks while the key is held, too: see RecentInput
            received.look()
            time.sleep(0.1)
        self.assertEqual(remote.returncode, 0, remote.stderr.read())
        received.look()
        times = [t for t, _ in received.key_events()]
        events = [event for _, event in received.key_events()]
        self.assertEqual(len(events), 3, "down, repeat, up")
        # The repeat comes after the long-press timeout. The release comes as long after it
        # before API 30 (monkey), and right after it from 30 on (input keyevent --longpress).
        gaps = [times[1] - times[0]]
        if int(self.adb_shell("getprop", "ro.build.version.sdk")) < 30:
            gaps.append(times[2] - times[1])
        for gap in gaps:
            self.assertGreaterEqual(gap, timeout - 5, times)
            self.assertLess(gap, timeout + 1000, times)
        if "keyCode=" in events[0]:   # Android 9 and older print the details
            details = [re.search(r"action=(\w+),.* flags=0x(\w+),.* keyCode=(\d+),.* repeatCount=(\d+)",
                                 e).groups() for e in events]
            self.assertEqual([(action in ("0", "DOWN"), int(code), int(repeat),
                               bool(int(flags, 16) & 0x80)) for action, flags, code, repeat in details],
                             [(True, 19, 0, False), (True, 19, 1, True), (False, 19, 0, False)],
                             "down, a repeat flagged FLAG_LONG_PRESS, up")
        self.assertEqual([self.adb_shell("settings", "get", "system", name).strip()
                          for name in ("accelerometer_rotation", "user_rotation")], rotation,
                         "the rotation settings monkey changes are put back")
        self.assertNotIn("adt-long-press", self.adb_shell("ls", "/data/local/tmp"))


@unittest.skipUnless(ENABLED, "real-emulator tests run only with: tests/run.py --emulator")
class StopOnTheRealEmulator(unittest.TestCase):
    def setUp(self):
        self.avd = default_avd()
        if serial_of(self.avd):
            self.skipTest(f"{self.avd} is running, and a test doesn't stop the developer's "
                          "emulator")
        self.addCleanup(subprocess.run, [str(BIN / "stop-emulator.sh"), self.avd],
                        capture_output=True, timeout=180)

    def start_and_get_pid(self):
        """Boots the AVD; returns its serial and its emulator's PID, from the AVD's lock file."""
        started = start(self.avd)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.lock = os.path.join(lib(f"avd_dir {self.avd}"), "hardware-qemu.ini.lock")
        with open(self.lock, "rb") as f:
            return started.stdout.strip(), int(f.read().strip(b"\0").decode())

    def test_stop_kills_an_emulator_that_does_not_answer(self):
        serial, pid = self.start_and_get_pid()
        os.kill(pid, signal.SIGSTOP)   # frozen: its console answers nothing, SIGTERM waits
        result = subprocess.run([str(BIN / "stop-emulator.sh"), self.avd], capture_output=True,
                                text=True, timeout=180, env={**os.environ, "ADT_STOP_TIMEOUT": "5"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("killed (SIGKILL)", result.stderr)
        self.assertTrue(exited(pid), "returned before the emulator had exited")
        again = start(self.avd)   # the lock file it left behind doesn't stop a new start
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_stop_waits_for_a_read_only_emulator(self):
        # -read-only writes no lock file: the emulator's console names its process instead.
        started = start(self.avd, "-read-only")
        self.assertEqual(started.returncode, 0, started.stderr)
        serial = started.stdout.strip()
        self.assertFalse(os.path.exists(os.path.join(lib(f"avd_dir {self.avd}"),
                                                     "hardware-qemu.ini.lock")))
        [pid] = emulator_pids("-avd", self.avd, "-read-only")
        result = subprocess.run([str(BIN / "stop-emulator.sh"), self.avd], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, f"Stopped '{self.avd}' ({serial}).\n")
        self.assertTrue(exited(pid), "returned before the emulator had exited")

    def test_stop_returns_once_the_avd_can_start_again(self):
        avd = self.avd
        serial, pid = self.start_and_get_pid()
        lock = self.lock

        result = subprocess.run([str(BIN / "stop-emulator.sh"), avd], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Stopped '{avd}' ({serial}).", result.stderr)
        self.assertTrue(exited(pid), "returned before the emulator had exited")
        self.assertFalse(os.path.exists(lock))
        self.assertNotIn(serial, lib("running_emulators").split())

        again = start(avd)   # right away: the AVD's files are free
        self.assertEqual(again.returncode, 0, again.stderr)
        result = subprocess.run([str(BIN / "stop-emulator.sh"), avd], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(serial_of(avd))
