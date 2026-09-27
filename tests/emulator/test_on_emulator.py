"""The scripts against the real SDK and emulator: slow (a boot), so only with run.py --emulator.

Uses the developer's own setup (SDK, AVD) exactly as the scripts find it; set ADT_AVD to pick
the AVD. If that AVD is already running it's reused and left running; otherwise it's booted
without a window and stopped afterwards (without saving a snapshot, so the next normal start is
unaffected). The stop-emulator.sh test needs an AVD of its own to stop, so it skips if the AVD
was already running.
"""
import os
import re
import signal
import subprocess
import time
import unittest

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


def start(avd):
    """Boots the AVD the way this tier does: no window, no snapshot saved when it's stopped."""
    return subprocess.run([str(BIN / "start-emulator.sh"), avd, "-no-window", "-no-snapshot-save"],
                          capture_output=True, text=True, timeout=600)


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

    def recent_key_events(self):
        """(age in ms, keyCode, is key-down) of each key event in the input dispatcher's recent
        queue, oldest first. Android 5-8 print a key-down as action=0, 9 as action=DOWN, and 10
        and newer print no details ("KeyEvent, age=12ms"): keyCode and is key-down are None."""
        dump = self.adb_shell("dumpsys", "input")
        queue = dump.split("RecentQueue", 1)[1].split("PendingEvent", 1)[0]
        events = []
        for line in queue.splitlines():
            if line.strip().startswith("KeyEvent"):
                code = re.search(r"keyCode=(\d+)", line)
                events.append((float(re.search(r"age=([\d.]+)ms", line).group(1)),
                               int(code.group(1)) if code else None,
                               bool(re.search(r"action=(?:0|DOWN),", line)) if code else None))
        return events

    def key_downs_since(self, sent_at):
        """keyCodes of the key-downs from after `sent_at` (a time.time()), oldest first; where
        Android prints no details, one None per press (a down and an up)."""
        events = self.recent_key_events()
        elapsed_ms = (time.time() - sent_at) * 1000   # after the dump, so no new event is left out
        new = [(code, down) for age, code, down in events if age < elapsed_ms]
        if new and new[0][0] is None:
            return [None] * (len(new) // 2)
        return [code for code, down in new if down]

    def wait_until_keys_are_quiet(self):
        """Waits until the newest key event is QUIET_MS old, so that the next batch's time window
        (which may reach back by as long as a dumpsys takes) can't take in older events."""
        deadline = time.time() + 10
        while any(age < QUIET_MS for age, _, _ in self.recent_key_events()):
            self.assertLess(time.time(), deadline, "key events keep arriving")
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
        # The recent queue holds the last 10 events (5 presses), so check a few keys at a time.
        # Android 11 also queues focus changes there (e.g. when OK opens something), so 3 at most.
        details = int(self.adb_shell("getprop", "ro.build.version.sdk")) < 29
        for first in range(0, len(KEYS), 3):
            batch = KEYS[first:first + 3]
            with self.subTest(keys=[code for _, code in batch]):
                self.wait_until_keys_are_quiet()
                sent_at = time.time()
                result = subprocess.run([str(BIN / "remote.sh"), self.serial],
                                        input="".join(k for k, _ in batch) + "q",
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                # Where Android prints no key codes, only the number of presses can be checked.
                expected = [code if details else None for _, code in batch]
                deadline = time.time() + 5
                while self.key_downs_since(sent_at) != expected and time.time() < deadline:
                    time.sleep(0.2)
                self.assertEqual(self.key_downs_since(sent_at), expected)


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
