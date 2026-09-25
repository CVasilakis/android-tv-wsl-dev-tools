"""The scripts against the real SDK and emulator: slow (a boot), so only with run.py --emulator.

Uses the developer's own setup (SDK, AVD) exactly as the scripts find it; set ADT_AVD to pick
the AVD. If that AVD is already running it's reused and left running; otherwise it's booted
without a window and stopped afterwards (without saving a snapshot, so the next normal start is
unaffected).
"""
import os
import re
import subprocess
import time
import unittest

from support.sandbox import BIN, LIB

ENABLED = os.environ.get("ADT_EMULATOR_TESTS") == "1"

# Keyboard input to remote.sh -> Android KeyEvent keyCode that must arrive on the device.
KEYS = [("\x1b[A", 19), ("\x1b[B", 20), ("\x1b[D", 21), ("\x1b[C", 22), ("\n", 23),
        ("\x7f", 4), ("m", 82), ("p", 85), ("+", 24), ("-", 25), ("h", 3)]


def lib(snippet):
    """Runs a bash snippet with lib.sh sourced, in the developer's real environment."""
    return subprocess.run(["bash", "-c", f"source {LIB}\n{snippet}"],
                          capture_output=True, text=True, timeout=60).stdout.strip()


@unittest.skipUnless(ENABLED, "real-emulator tests run only with: tests/run.py --emulator")
class OnTheRealEmulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adb = lib('echo "$ADB"')
        cls.avd = os.environ.get("ADT_AVD") or lib(
            'avds="$(list_avds)"; grep -qx tv_api25 <<<"$avds" && echo tv_api25 || head -n1 <<<"$avds"')
        cls.serial = cls.find_serial()
        cls.started_here = cls.serial is None
        if cls.started_here:
            cls.start_result = subprocess.run(
                [str(BIN / "start-emulator.sh"), cls.avd, "-no-window", "-no-snapshot-save"],
                capture_output=True, text=True, timeout=600)
            if cls.start_result.returncode != 0:
                raise RuntimeError(f"start-emulator.sh failed:\n{cls.start_result.stdout}"
                                   f"{cls.start_result.stderr}")
            cls.serial = cls.find_serial()

    @classmethod
    def tearDownClass(cls):
        if cls.started_here and cls.serial:
            subprocess.run([cls.adb, "-s", cls.serial, "emu", "kill"], capture_output=True)

    @classmethod
    def find_serial(cls):
        return lib(f'for s in $(running_emulators); do [ "$(emulator_avd "$s")" = {cls.avd} ] '
                   '&& echo "$s"; done') or None

    def adb_shell(self, *args):
        return subprocess.run([self.adb, "-s", self.serial, "shell", *args], capture_output=True,
                              text=True, timeout=60).stdout

    def recent_key_downs(self):
        """keyCodes of the key-down events in the input dispatcher's recent queue, oldest first."""
        dump = self.adb_shell("dumpsys", "input")
        queue = dump.split("RecentQueue", 1)[1].split("PendingEvent", 1)[0]
        return [int(code) for code in re.findall(r"KeyEvent\(.*?action=0,.*?keyCode=(\d+)", queue)]

    def test_start_returns_once_android_has_booted(self):
        if not self.started_here:
            self.skipTest(f"{self.avd} was already running")
        self.assertEqual(self.adb_shell("getprop", "sys.boot_completed").strip(), "1")
        self.assertIn(f"booted as {self.serial}", self.start_result.stdout)

    def test_start_again_reports_the_running_emulator(self):
        result = subprocess.run([str(BIN / "start-emulator.sh"), self.avd],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"already running as {self.serial}", result.stdout)

    def test_remote_keys_arrive_as_the_right_android_keys(self):
        # The recent queue holds the last 10 events (5 presses), so check a few keys at a time.
        for start in range(0, len(KEYS), 4):
            batch = KEYS[start:start + 4]
            with self.subTest(keys=[code for _, code in batch]):
                result = subprocess.run([str(BIN / "remote.sh"), self.serial],
                                        input="".join(k for k, _ in batch) + "q",
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = [code for _, code in batch]
                deadline = time.time() + 5
                while self.recent_key_downs()[-len(expected):] != expected and time.time() < deadline:
                    time.sleep(0.2)
                self.assertEqual(self.recent_key_downs()[-len(expected):], expected)
