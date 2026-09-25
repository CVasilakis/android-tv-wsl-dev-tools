"""remote.sh: which emulator it talks to, and which key each keyboard key sends."""
import subprocess
import time

from support.sandbox import ScriptTestCase

# Keyboard input -> Android key sent with `adb shell input keyevent` (see remote.sh).
KEYS = {
    "\x1b[A": "DPAD_UP",           # Up
    "\x1b[B": "DPAD_DOWN",         # Down
    "\x1b[C": "DPAD_RIGHT",        # Right
    "\x1b[D": "DPAD_LEFT",         # Left
    "\n": "DPAD_CENTER",           # Enter: OK
    "\x7f": "BACK",                # Backspace
    "\x1b": "BACK",                # Esc alone
    "h": "HOME",
    "m": "MENU",
    "p": "MEDIA_PLAY_PAUSE",
    "+": "VOLUME_UP",
    "=": "VOLUME_UP",              # '+' without Shift
    "-": "VOLUME_DOWN",
}


class RemoteTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)

    def presses(self):
        """(serial, Android key) of each key press sent, in order."""
        return [(call["argv"][1], call["argv"][5]) for call in self.sandbox.calls("adb")
                if call["argv"][2:5] == ["shell", "input", "keyevent"]]


class SendsTheRightKeys(RemoteTestCase):
    def setUp(self):
        super().setUp()
        self.serial = self.sandbox.start_emulator("tv_api25")

    def test_each_key(self):
        for key, name in KEYS.items():
            with self.subTest(key=repr(key)):
                before = len(self.presses())
                self.assertSucceeded(self.sandbox.run("remote.sh", input=key))
                self.assertEqual(self.presses()[before:], [(self.serial, name)])

    def test_a_sequence_in_order(self):
        self.sandbox.run("remote.sh", input="\x1b[B\x1b[B\x1b[C\nh")
        self.assertEqual([key for _, key in self.presses()],
                         ["DPAD_DOWN", "DPAD_DOWN", "DPAD_RIGHT", "DPAD_CENTER", "HOME"])

    def test_keys_typed_ahead_are_not_lost(self):
        # adb shell reads its stdin: a key press must not swallow the keys waiting after it.
        self.sandbox.run("remote.sh", input="hmp")
        self.assertEqual([key for _, key in self.presses()], ["HOME", "MENU", "MEDIA_PLAY_PAUSE"])

    def test_q_quits_and_ignores_what_follows(self):
        result = self.sandbox.run("remote.sh", input="hqh")
        self.assertSucceeded(result)
        self.assertEqual([key for _, key in self.presses()], ["HOME"])

    def test_other_keys_send_nothing(self):
        self.sandbox.run("remote.sh", input="xyz 1\t")
        self.assertEqual(self.presses(), [])

    def test_esc_then_another_key_typed_later_are_two_presses(self):
        # A human types Esc and h far apart; remote.sh must not read them as one escape sequence.
        process = subprocess.Popen([str(self.sandbox.tools / "bin" / "remote.sh")],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=self.sandbox.env(), text=True)
        process.stdin.write("\x1b")
        process.stdin.flush()
        time.sleep(0.5)
        process.stdin.write("h")
        process.stdin.close()
        process.wait(timeout=10)
        self.assertEqual([key for _, key in self.presses()], ["BACK", "HOME"])

    def test_keeps_going_after_a_failed_adb_call(self):
        self.sandbox.set_behavior(keyevent_failures=1)
        result = self.sandbox.run("remote.sh", input="hmp")
        self.assertSucceeded(result)
        self.assertEqual([key for _, key in self.presses()], ["HOME", "MENU", "MEDIA_PLAY_PAUSE"],
                         "all three were sent even though the first one failed")


class ChoosesTheEmulator(RemoteTestCase):
    def test_the_only_running_emulator(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("remote.sh", input="h")
        self.assertIn(f"Controlling {serial}", result.out)
        self.assertEqual(self.presses(), [(serial, "HOME")])

    def test_ignores_physical_devices(self):
        self.sandbox.connect_device("R58M123ABC")
        serial = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", input="h")
        self.assertEqual(self.presses(), [(serial, "HOME")])

    def test_android_serial_picks_one_of_several(self):
        self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", input="h", env={"ANDROID_SERIAL": tv})
        self.assertEqual(self.presses(), [(tv, "HOME")])

    def test_argument_wins_over_android_serial(self):
        phone = self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", tv, input="h", env={"ANDROID_SERIAL": phone})
        self.assertEqual(self.presses(), [(tv, "HOME")])

    def test_several_emulators_without_a_choice(self):
        self.sandbox.start_emulator("phone")
        self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("remote.sh", input="h")
        self.assertFailed(result, "several emulators are running (emulator-5554 emulator-5556)")
        self.assertEqual(self.presses(), [])

    def test_no_emulator_running(self):
        self.sandbox.connect_device("R58M123ABC")
        self.assertFailed(self.sandbox.run("remote.sh", input="h"), "no running emulator")

    def test_a_physical_device_when_named(self):
        self.sandbox.start_emulator("tv_api25")
        self.sandbox.connect_device("R58M123ABC")
        self.assertSucceeded(self.sandbox.run("remote.sh", "R58M123ABC", input="h"))
        self.assertEqual(self.presses(), [("R58M123ABC", "HOME")])

    def test_unknown_option_prints_usage(self):
        self.assertFailed(self.sandbox.run("remote.sh", "--fast"), "Usage:", code=2)
