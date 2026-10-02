"""The scripts against the real SDK and emulator: slow (a boot), so only with run.py --emulator.

Uses the developer's own setup (SDK, AVD) exactly as the scripts find it; set ADT_AVD to pick
the AVD. If that AVD is already running it's reused and left running; otherwise it's booted
without a window, with --wait-for-home so the tests start on a device that has settled, and
stopped afterwards (without saving a snapshot, so the next normal start is unaffected). The stop-emulator.sh tests need an AVD of their own to stop, so they skip if the
AVD was already running.
"""
import os
import re
import signal
import subprocess
import time
import unittest

from support.input_dump import RecentInput, input_is_quiet, recent_queue
from support.sandbox import BIN, LIB

ENABLED = os.environ.get("ADT_EMULATOR_TESTS") == "1"
QUIET_MS = 1000

# How long a test waits for something on the device, in seconds. The waits end as soon as it's
# there, so these limits only decide how long a failure takes, and must hold for an emulator
# starved of CPU (-cores 1 on a busy host). There, `adb shell input keyevent` alone can take over
# 15 s to start `input` on the device, `input` can send a key's release seconds after its press,
# and a look at `dumpsys input` can take 15 s (dumpsys gives up on a service after 10 s). Android
# holds a key for up to 5 s while a window it's going to starts (5 s x ro.hw_timeout_multiplier,
# which no tested image sets, so 1).
KEY_TIMEOUT = 60     # from typing a key until a look sees it: remote.sh's adb call, the hold, a look
QUIET_TIMEOUT = 30   # for the input dispatcher to settle: a held key, QUIET_MS, then a look
FOCUS_TIMEOUT = 30   # for an app's window to come to the front (am start, the Home key)
SETTLE_TIMEOUT = 120   # for Settings to stay in front, opened again while other apps come over it
STABLE_LOOKS = 3       # looks in a row, STABLE_INTERVAL s apart, that show an app stays in front
STABLE_INTERVAL = 2
LONG_PRESS_TIMEOUT = 180   # for remote.sh --long-press: up to 9 adb calls, monkey's among them

# Python's limits for the scripts, from the scripts' own: when Python stops a script on the way,
# its emulator can be left running (start-emulator.sh starts it with nohup), so a script must
# always reach its own limits first, and clean up and say why. Each adb call they make gets
# ADB_CALL s (adb_bounded in lib.sh: ADB_TIMEOUT s, then SIGKILL ADB_KILL_AFTER s later), and each
# of their limits lasts up to 1 s longer (time_is_up in lib.sh). ADB_CALLS is more than either
# script makes on the way with up to three emulators running. The scripts' values are copied here;
# hermetic/test_conventions.py checks that the copies match.
ADB_TIMEOUT = 15
ADB_KILL_AFTER = 5
ADB_CALL = ADB_TIMEOUT + ADB_KILL_AFTER
ADB_CALLS = 10
SAVE_TIMEOUT = 30  # start-emulator.sh's, on API 26 and 27, for Android to save a setting
TERM_WAIT = 30   # start-emulator.sh's, for an emulator it stops to exit after SIGTERM
KILL_WAIT = 30   # stop-emulator.sh's, for a killed emulator to exit, and again for adb to unlist it
STOP_SAVE_WAIT = 12  # stop-emulator.sh's SAVE_WAIT, from API 33 on, for Android to save app states
BOOT_TIMEOUT = int(os.environ.get("ADT_BOOT_TIMEOUT") or 900)   # passed on to start-emulator.sh
HOME_TIMEOUT = int(os.environ.get("ADT_HOME_TIMEOUT") or 300)   # and to its --wait-for-home
STOP_TIMEOUT = int(os.environ.get("ADT_STOP_TIMEOUT") or 60)    # passed on to stop-emulator.sh
# lib.sh's wait_for_home: its limit, then its pause between two looks and a last look of up to
# five adb calls (the API level, what HOME resolves to, two dumps, a Back).
HOME_LIMIT = HOME_TIMEOUT + 1 + 1 + 5 * ADB_CALL if HOME_TIMEOUT else None
# start-emulator.sh's wait for Android to save a setting, on API 26 and 27: its limit, then its
# pause between two looks and a last look (one adb call).
SAVE_LIMIT = SAVE_TIMEOUT + 1 + 1 + ADB_CALL
# start-emulator.sh: the boot limit; on API 26 and 27, the wait for Android to save a setting;
# with --wait-for-home, that wait; then stopping an emulator that didn't boot or settle (SIGTERM,
# TERM_WAIT s, then SIGKILL). No limit when a limit is 0 (the script's own "no limit").
START_LIMIT = (BOOT_TIMEOUT + 1 + SAVE_LIMIT + HOME_LIMIT + TERM_WAIT + 1 + ADB_CALLS * ADB_CALL
               if BOOT_TIMEOUT and HOME_LIMIT else None)

# Keyboard input to remote.sh -> Android KeyEvent keyCode that must arrive on the device.
KEYS = [("\x1b[A", 19), ("\x1b[B", 20), ("\x1b[D", 21), ("\x1b[C", 22), ("\n", 23),
        ("\x7f", 4), ("m", 82), ("p", 85), ("+", 24), ("-", 25), ("h", 3)]


def named_component(output):
    """The activity a `cmd package resolve-activity --brief` output, or a window's title, names,
    with its class name in full (com.android.tv.settings/com.android.tv.settings.MainSettings, as
    `dumpsys window` names windows), or None."""
    found = re.findall(r"^([\w.]+)/([\w.$]+)\s*$", output, re.MULTILINE)
    if not found:
        return None
    package, name = found[-1]
    return f"{package}/{package}{name}" if name.startswith(".") else f"{package}/{name}"


def package(component):
    """The package of a component (com.android.tv.settings/.MainSettings), or None."""
    return component.split("/")[0] if component else None


def focus(dump):
    """What's in front, from a `dumpsys window`: the activity of the window that has focus, and
    the focused activity, the one Android brought to the front, whose window gets focus only once
    it's drawn (on a starved device, the home app's still had none after 30 s). Each like
    com.android.tv.settings/com.android.tv.settings.MainSettings, or None (no window has focus,
    or it isn't an activity's, like an ANR dialog). Then the dump's lines on focus, to show in a
    failure. The last lines: after an ANR the dump starts with a copy of the state at that time
    (WINDOW MANAGER LAST ANR), focus included."""
    window, app = (([None] + re.findall(rf"{name}=.*", dump))[-1] for name in ("mCurrentFocus",
                                                                              "mFocusedApp"))
    shown = "\n".join(line for line in (window, app) if line)
    window = window and re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+/[\w.$]+)\}", window)
    app = app and re.search(r"mFocusedApp=.*?ActivityRecord\{\S+ \S+ ([\w.]+/[\w.$]+)", app)
    return (window and named_component(window.group(1)), app and named_component(app.group(1)),
            shown or dump[-500:])


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


def start_script(*args):
    """Runs start-emulator.sh, which gives up on a boot before Python gives up on it."""
    return subprocess.run([str(BIN / "start-emulator.sh"), *args], capture_output=True, text=True,
                          timeout=START_LIMIT, env={**os.environ, "ADT_BOOT_TIMEOUT": str(BOOT_TIMEOUT),
                                                    "ADT_HOME_TIMEOUT": str(HOME_TIMEOUT)})


def wait_for_home(serial):
    """lib.sh's wait_for_home, which start-emulator.sh --wait-for-home runs: waits until the home
    app's screen is at the top, done starting, with the focus, at two looks in a row. Returns its
    package, or None and what was in front instead."""
    result = subprocess.run(["bash", "-c", f'source {LIB}\nwait_for_home "$1" "$2"', "bash", serial,
                             str(HOME_TIMEOUT)], capture_output=True, text=True, timeout=HOME_LIMIT)
    return (result.stdout.strip(), None) if result.returncode == 0 else (None, result.stdout)


def start(avd, *flags):
    """Boots the AVD the way this tier does: no window, no snapshot saved when it's stopped."""
    return start_script(avd, "-no-window", "-no-snapshot-save", *flags)


def stop(target, stop_timeout=STOP_TIMEOUT):
    """Runs stop-emulator.sh, which returns once the emulator has exited, with Python's limit
    longer than its longest run: the save before the stop (from API 33 on, STOP_SAVE_WAIT s,
    its pause between two looks and a last look; up to API 31, a pause of 1 s), stop_timeout s for
    the emulator to exit (after adb emu kill, or SIGTERM), KILL_WAIT for it to exit after SIGKILL,
    KILL_WAIT again for adb to unlist it, and its adb calls."""
    save = STOP_SAVE_WAIT + 1 + 1 + ADB_CALL
    limit = save + stop_timeout + 2 * KILL_WAIT + 3 + ADB_CALLS * ADB_CALL
    return subprocess.run([str(BIN / "stop-emulator.sh"), target], capture_output=True, text=True,
                          timeout=limit, env={**os.environ, "ADT_STOP_TIMEOUT": str(stop_timeout)})


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


@unittest.skipUnless(ENABLED, "real-emulator tests run only with: tests/run.py --emulator")
class OnTheRealEmulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adb = lib('echo "$ADB"')
        cls.avd = default_avd()
        cls.serial = serial_of(cls.avd)
        cls.started_here = cls.serial is None
        if cls.started_here:
            # stop-emulator.sh waits until it has exited: the next test class may boot the same
            # AVD. A class cleanup, not tearDownClass, which isn't called when setUpClass fails,
            # and registered before the boot, which can fail with the emulator still running.
            cls.addClassCleanup(stop, cls.avd)
            cls.start_result = start(cls.avd, "--wait-for-home")
            if cls.start_result.returncode != 0:
                raise RuntimeError(f"start-emulator.sh failed:\n{cls.start_result.stdout}"
                                   f"{cls.start_result.stderr}")
            cls.serial = serial_of(cls.avd)
            cls.focus_after_start = focus(subprocess.run(
                [cls.adb, "-s", cls.serial, "shell", "dumpsys", "window"], capture_output=True,
                text=True, timeout=60).stdout)

    def adb_shell(self, *args):
        return subprocess.run([self.adb, "-s", self.serial, "shell", *args], capture_output=True,
                              text=True, timeout=60).stdout

    def wait_until_input_is_quiet(self):
        """Waits until the input dispatcher has no event waiting, and its newest event, of any
        kind, is QUIET_MS old: no key from before, and no focus change of a window a test before
        opened, can then be taken for one that comes after, or push it out of the recent queue
        (see RecentInput). A key held for a starting window would enter the queue only later."""
        deadline = time.monotonic() + QUIET_TIMEOUT
        while not input_is_quiet(dump := self.adb_shell("dumpsys", "input"), QUIET_MS):
            if time.monotonic() > deadline:
                if recent_queue(dump) is None:
                    self.fail(f"`dumpsys input` showed no recent queue for {QUIET_TIMEOUT} s (on a "
                              f"starved device dumpsys gives up after 10 s):\n{dump[-500:]}")
                self.fail(f"input events kept coming for {QUIET_TIMEOUT} s, or one kept waiting "
                          "(PendingEvent, InboundQueue)")
            time.sleep(0.2)

    def test_start_returns_once_android_has_booted(self):
        if not self.started_here:
            self.skipTest(f"{self.avd} was already running")
        self.assertEqual(self.adb_shell("getprop", "sys.boot_completed").strip(), "1")
        self.assertEqual(self.start_result.stdout, f"{self.serial}\n", "stdout is only the serial")
        self.assertIn(f"booted as {self.serial}", self.start_result.stderr)

    def test_start_waits_for_home_until_the_home_app_has_the_focus(self):
        # What was in front when start-emulator.sh --wait-for-home returned.
        if not self.started_here:
            self.skipTest(f"{self.avd} was already running")
        home = re.search(r"The home app \((\S+)\) is in front", self.start_result.stderr)
        self.assertTrue(home, self.start_result.stderr)
        window, app, shown = self.focus_after_start
        self.assertEqual((package(window), package(app)), (home.group(1),) * 2, shown)

    def test_start_again_reports_the_running_emulator(self):
        result = start_script(self.avd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{self.serial}\n")
        self.assertIn(f"already running as {self.serial}", result.stderr)

    def front(self):
        """What's in front now: see focus()."""
        return focus(self.adb_shell("dumpsys", "window"))

    def resolved(self, *intent):
        """The activity an intent opens, from `cmd package resolve-activity`: the one of highest
        priority, or the chooser (android/com.android.internal.app.ResolverActivity) when there's
        no single one. None on API 22 and 23, which have no `cmd`."""
        if int(self.adb_shell("getprop", "ro.build.version.sdk")) < 24:
            return None
        return named_component(self.adb_shell("cmd", "package", "resolve-activity", "--brief",
                                              *intent))

    def test_remote_home_key_leaves_an_app(self):
        # start-emulator.sh first: on API 26 and 27 it's what makes Home work (see its header),
        # and an emulator that was already running may not have been started by it.
        start_script(self.avd)
        # The package Home opens: the home app, whose sign-in screen Google TV without an account
        # shows instead of a home screen, or the chooser (package android) on API 22 with another
        # home app installed. wait_for_home names it once it's in front and has settled: after a
        # boot, from API 24 on, a HOME intent resolves to Settings' FallbackHome until the user is
        # unlocked, and the home app may lack the focus for a while. A HOME intent (not the Home
        # key) first brings it to the front, where an emulator that was already running may show
        # another app.
        self.adb_shell("am", "start", "-a", "android.intent.action.MAIN",
                       "-c", "android.intent.category.HOME")
        home, shown = wait_for_home(self.serial)
        self.assertTrue(home, f"the home app didn't settle in front within {HOME_TIMEOUT} s: {shown}")
        settings = self.resolved("-a", "android.settings.SETTINGS")
        # Settings must be in front, and stay there, before Home: the home app (or Google TV's
        # sign-in screen) could still come to the front by itself, as if Home had worked. So
        # Settings is opened again until it's in front at STABLE_LOOKS looks in a row: its own
        # activity where `cmd` can name it, else any app but the home app. Without -W: on API 22,
        # with the chooser in front, `am start -W` of an app open behind it never returns.
        self.adb_shell("am", "start", "-a", "android.settings.SETTINGS")
        in_front, looks = None, 0
        deadline = time.monotonic() + SETTLE_TIMEOUT
        while looks < STABLE_LOOKS:
            time.sleep(STABLE_INTERVAL)
            focused, _, shown = self.front()
            if focused and package(focused) != home and settings in (None, focused):
                looks = looks + 1 if focused == in_front else 1
            else:
                looks = 0
                if time.monotonic() > deadline:
                    self.fail(f"Settings ({settings or 'any app'}) didn't stay in front of the home "
                              f"app ({home}) within {SETTLE_TIMEOUT} s:\n{shown}")
                self.adb_shell("am", "start", "-a", "android.settings.SETTINGS")
            in_front = focused
        result = subprocess.run([str(BIN / "remote.sh"), self.serial], input="hq",
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The home app's activity in front is enough: its window may not have focus yet.
        deadline = time.monotonic() + FOCUS_TIMEOUT
        while package((front := self.front())[1]) != home:
            if time.monotonic() > deadline:
                self.fail(f"Home didn't bring the home app ({home}) to the front:\n{front[2]}")
            time.sleep(0.2)

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
            deadline = time.monotonic() + KEY_TIMEOUT
            while len(received.look().key_downs()) < len(expected) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertEqual(received.key_downs(), expected, f"after the key for {code}")
            # Typed as soon as the key before arrived, two presses can come closer together than
            # a look can place them, and a look that also sees new window focus changes can't
            # tell them apart (Google TV API 33: 42 ms apart).
            time.sleep(0.3)
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
        deadline = time.monotonic() + LONG_PRESS_TIMEOUT
        while remote.poll() is None:   # looks while the key is held, too: see RecentInput
            self.assertLess(time.monotonic(), deadline, "remote.sh --long-press didn't finish")
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
        self.addCleanup(stop, self.avd)

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
        result = stop(self.avd, stop_timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("killed (SIGKILL)", result.stderr)
        self.assertTrue(exited(pid), "returned before the emulator had exited")
        again = start(self.avd)   # the lock file it left behind doesn't stop a new start
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_stop_kills_a_read_only_emulator_that_does_not_answer(self):
        # -read-only writes no lock file, and a frozen console can't name the process: the
        # emulator's discovery file names it, found by the AVD's name.
        started = start(self.avd, "-read-only")
        self.assertEqual(started.returncode, 0, started.stderr)
        [pid] = emulator_pids("-avd", self.avd, "-read-only")
        self.addCleanup(lambda: exited(pid) or os.kill(pid, signal.SIGKILL))   # if left frozen
        os.kill(pid, signal.SIGSTOP)
        result = stop(self.avd, stop_timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("killed (SIGKILL)", result.stderr)
        self.assertTrue(exited(pid), "returned before the emulator had exited")

    def test_stop_waits_for_a_read_only_emulator(self):
        # -read-only writes no lock file: the emulator's console names its process instead.
        started = start(self.avd, "-read-only")
        self.assertEqual(started.returncode, 0, started.stderr)
        serial = started.stdout.strip()
        self.assertFalse(os.path.exists(os.path.join(lib(f"avd_dir {self.avd}"),
                                                     "hardware-qemu.ini.lock")))
        [pid] = emulator_pids("-avd", self.avd, "-read-only")
        result = stop(self.avd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, f"Stopped '{self.avd}' ({serial}).\n")
        self.assertTrue(exited(pid), "returned before the emulator had exited")

    def test_stop_an_emulator_that_is_exiting_already(self):
        # As when the test run that started it is killed: after SIGTERM its console answers
        # nothing, and adb lists it for a moment after its exit. Depending on when the script gets
        # there it sees it running, exiting or gone; never still running.
        started = start(self.avd, "-read-only")
        self.assertEqual(started.returncode, 0, started.stderr)
        serial = started.stdout.strip()
        [pid] = emulator_pids("-avd", self.avd, "-read-only")
        os.kill(pid, signal.SIGTERM)
        result = stop(serial)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("still running", result.stderr)
        self.assertNotIn(serial, lib("running_emulators").split())

    def test_stop_returns_once_the_avd_can_start_again(self):
        avd = self.avd
        serial, pid = self.start_and_get_pid()
        lock = self.lock

        result = stop(avd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Stopped '{avd}' ({serial}).", result.stderr)
        self.assertTrue(exited(pid), "returned before the emulator had exited")
        self.assertFalse(os.path.exists(lock))
        self.assertNotIn(serial, lib("running_emulators").split())

        again = start(avd)   # right away: the AVD's files are free
        self.assertEqual(again.returncode, 0, again.stderr)
        result = stop(avd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(serial_of(avd))
