"""remote.sh: which emulator it talks to, and which key each keyboard key sends."""
import subprocess
import time

from support.sandbox import ScriptTestCase

# Keyboard input -> Linux scan code the emulator must receive (see remote.sh).
KEYS = {
    "\x1b[A": 103,  # Up        -> DPAD_UP
    "\x1b[B": 108,  # Down      -> DPAD_DOWN
    "\x1b[C": 106,  # Right     -> DPAD_RIGHT
    "\x1b[D": 105,  # Left      -> DPAD_LEFT
    "\n": 232,      # Enter     -> DPAD_CENTER (OK)
    "\x7f": 158,    # Backspace -> BACK
    "h": 102,       # HOME
    "m": 139,       # MENU
    "p": 164,       # MEDIA_PLAY_PAUSE
    "+": 115,       # VOLUME_UP
    "=": 115,       # VOLUME_UP ('+' without Shift)
    "-": 114,       # VOLUME_DOWN
}


class RemoteTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)

    def presses(self):
        """(serial, scan code) of each key press sent, in order."""
        result = []
        for call in self.sandbox.calls("adb"):
            argv = call["argv"]
            if argv[2:5] == ["emu", "event", "send"]:
                down, up = argv[5:]
                code = int(down.split(":")[1])
                self.assertEqual((down, up), (f"EV_KEY:{code}:1", f"EV_KEY:{code}:0"),
                                 "each press is a key down followed by a key up")
                result.append((argv[1], code))
        return result


class SendsTheRightKeys(RemoteTestCase):
    def setUp(self):
        super().setUp()
        self.serial = self.sandbox.start_emulator("tv_api25")

    def test_each_key(self):
        for key, code in KEYS.items():
            with self.subTest(key=repr(key)):
                before = len(self.presses())
                self.assertSucceeded(self.sandbox.run("remote.sh", input=key))
                self.assertEqual(self.presses()[before:], [(self.serial, code)])

    def test_a_sequence_in_order(self):
        self.sandbox.run("remote.sh", input="\x1b[B\x1b[B\x1b[C\nh")
        self.assertEqual([code for _, code in self.presses()], [108, 108, 106, 232, 102])

    def test_q_quits_and_ignores_what_follows(self):
        result = self.sandbox.run("remote.sh", input="hqh")
        self.assertSucceeded(result)
        self.assertEqual([code for _, code in self.presses()], [102])

    def test_other_keys_send_nothing(self):
        self.sandbox.run("remote.sh", input="xyz 1\t")
        self.assertEqual(self.presses(), [])

    def test_lone_esc_is_back(self):
        self.sandbox.run("remote.sh", input="\x1b")
        self.assertEqual([code for _, code in self.presses()], [158])

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
        self.assertEqual([code for _, code in self.presses()], [158, 102])

    def test_keeps_going_after_a_failed_adb_call(self):
        self.sandbox.set_behavior(event_send_failures=1)
        result = self.sandbox.run("remote.sh", input="hmp")
        self.assertSucceeded(result)
        self.assertEqual([code for _, code in self.presses()], [102, 139, 164],
                         "all three were sent even though the first one failed")


class ChoosesTheEmulator(RemoteTestCase):
    def test_the_only_running_emulator(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("remote.sh", input="h")
        self.assertIn(f"Controlling {serial}", result.out)
        self.assertEqual(self.presses(), [(serial, 102)])

    def test_ignores_physical_devices(self):
        self.sandbox.connect_device("R58M123ABC")
        serial = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", input="h")
        self.assertEqual(self.presses(), [(serial, 102)])

    def test_android_serial_picks_one_of_several(self):
        self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", input="h", env={"ANDROID_SERIAL": tv})
        self.assertEqual(self.presses(), [(tv, 102)])

    def test_argument_wins_over_android_serial(self):
        phone = self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("remote.sh", tv, input="h", env={"ANDROID_SERIAL": phone})
        self.assertEqual(self.presses(), [(tv, 102)])

    def test_several_emulators_without_a_choice(self):
        self.sandbox.start_emulator("phone")
        self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("remote.sh", input="h")
        self.assertFailed(result, "several emulators are running (emulator-5554 emulator-5556)")
        self.assertEqual(self.presses(), [])

    def test_no_emulator_running(self):
        self.sandbox.connect_device("R58M123ABC")
        self.assertFailed(self.sandbox.run("remote.sh", input="h"), "no running emulator")

    def test_refuses_a_physical_device(self):
        self.sandbox.connect_device("R58M123ABC")
        result = self.sandbox.run("remote.sh", "R58M123ABC", input="h")
        self.assertFailed(result, "isn't an emulator")
        self.assertEqual(self.presses(), [])

    def test_unknown_option_prints_usage(self):
        self.assertFailed(self.sandbox.run("remote.sh", "--fast"), "Usage:", code=2)
