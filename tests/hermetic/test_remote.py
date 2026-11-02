"""tv-remote.sh: which emulator it talks to, which key each keyboard key sends, and long presses."""
import subprocess
from pathlib import Path

from support.sandbox import ScriptTestCase

LIB = Path(__file__).resolve().parents[2] / "lib"

# Keyboard input -> Android key sent with `adb shell input keyevent` (see tv-remote.sh).
KEYS = {
    "\x1b[A": "DPAD_UP",           # Up
    "\x1b[B": "DPAD_DOWN",         # Down
    "\x1b[C": "DPAD_RIGHT",        # Right
    "\x1b[D": "DPAD_LEFT",         # Left
    "\x1bOA": "DPAD_UP",           # Up, in a terminal's application mode
    "\x1bOD": "DPAD_LEFT",         # Left, the same
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

    def long_presses(self, serial):
        """(key code, ms until the repeat, ms held) of each long press the device received,
        checking that each one is what a held remote button sends: down; a repeat, which Android
        flags as a long press; up; all with the same down time."""
        events, presses = self.sandbox.device_state(serial)["key_events"], []
        self.assertEqual(len(events) % 3, 0, events)
        for down, repeat, up in zip(events[::3], events[1::3], events[2::3]):
            code, start = down["code"], down["at_ms"]
            self.assertEqual([(e["action"], e["code"], e["repeat"], e["long_press"], e["down_ms"])
                              for e in (down, repeat, up)],
                             [("down", code, 0, False, start), ("down", code, 1, True, start),
                              ("up", code, 0, False, start)])
            presses.append((code, repeat["at_ms"] - start, up["at_ms"] - start))
        return presses


class SendsTheRightKeys(RemoteTestCase):
    def setUp(self):
        super().setUp()
        self.serial = self.sandbox.start_emulator("tv_api25")

    def test_each_key(self):
        for key, name in KEYS.items():
            with self.subTest(key=repr(key)):
                before = len(self.presses())
                self.assertSucceeded(self.sandbox.run("tv-remote.sh", input=key))
                self.assertEqual(self.presses()[before:], [(self.serial, name)])

    def test_a_sequence_in_order(self):
        self.sandbox.run("tv-remote.sh", input="\x1b[B\x1b[B\x1b[C\nh")
        self.assertEqual([key for _, key in self.presses()],
                         ["DPAD_DOWN", "DPAD_DOWN", "DPAD_RIGHT", "DPAD_CENTER", "HOME"])

    def test_keys_typed_ahead_are_not_lost(self):
        # adb shell reads its stdin: a key press must not swallow the keys waiting after it.
        self.sandbox.run("tv-remote.sh", input="hmp")
        self.assertEqual([key for _, key in self.presses()], ["HOME", "MENU", "MEDIA_PLAY_PAUSE"])

    def test_q_quits_and_ignores_what_follows(self):
        result = self.sandbox.run("tv-remote.sh", input="hqh")
        self.assertSucceeded(result)
        self.assertEqual([key for _, key in self.presses()], ["HOME"])

    def test_other_keys_send_nothing(self):
        self.sandbox.run("tv-remote.sh", input="xyz 1\t\x1b[3~\x1bOP")   # ... Delete, F1
        self.assertEqual(self.presses(), [])

    def test_esc_alone_is_sent_before_the_next_key_is_typed(self):
        # A human types Esc and h far apart: the Esc, read from an input that stays open, must
        # be sent as Back without waiting for more keys (an arrow key's rest), before h is typed.
        process = subprocess.Popen([str(self.sandbox.tools / "bin" / "tv-remote.sh")],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=self.sandbox.env(), text=True)
        self.addCleanup(process.kill)
        process.stdin.write("\x1b")
        process.stdin.flush()
        self.wait_until(self.presses)
        self.assertEqual([key for _, key in self.presses()], ["BACK"],
                         "sent before h is typed")
        process.stdin.write("h")
        process.stdin.close()
        process.wait(timeout=10)
        self.assertEqual([key for _, key in self.presses()], ["BACK", "HOME"])

    def test_keys_right_after_esc_are_keys_of_their_own(self):
        # Typed ahead while the key before is being sent, or piped: only [ or O after an Esc
        # starts an arrow key.
        for keys, sent in [("\x1bh", ["BACK", "HOME"]), ("\x1b\x1b[A", ["BACK", "DPAD_UP"]),
                           ("\x1b\n", ["BACK", "DPAD_CENTER"]), ("\x1bqh", ["BACK"])]:
            with self.subTest(keys=repr(keys)):
                before = len(self.presses())
                self.assertSucceeded(self.sandbox.run("tv-remote.sh", input=keys))
                self.assertEqual([key for _, key in self.presses()[before:]], sent)

    def test_keeps_going_after_a_failed_adb_call(self):
        self.sandbox.set_behavior(keyevent_failures=1)
        result = self.sandbox.run("tv-remote.sh", input="hmp")
        self.assertSucceeded(result)
        self.assertEqual([key for _, key in self.presses()], ["HOME", "MENU", "MEDIA_PLAY_PAUSE"],
                         "all three were sent even though the first one failed")


class LongPress(RemoteTestCase):
    def setUp(self):
        super().setUp()
        self.set_long_press_timeout("400")
        self.serial = self.sandbox.start_emulator("tv_api25")

    def set_long_press_timeout(self, value):
        self.sandbox.set_behavior(device_settings={"long_press_timeout": value})

    def test_from_api_30_on_it_is_inputs_own_long_press(self):
        # There input holds the key, and a program of ours would be needless.
        self.sandbox.set_behavior(api_level=30)
        self.assertSucceeded(self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER"))
        self.assertEqual(self.long_presses(self.serial), [(23, 400, 400)])
        calls = " ".join(" ".join(argv) for argv in self.sandbox.argvs("adb"))
        self.assertNotIn("monkey", calls)
        self.assertNotIn("app_process", calls)

    def test_before_api_30_it_is_our_own_program_never_monkey(self):
        # monkey made the windows opened while it ran miss their focus on API 28, and on API 36
        # recreated the app in front; HoldKey injects the three events and nothing else runs.
        self.assertSucceeded(self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER"))
        calls = " ".join(" ".join(argv) for argv in self.sandbox.argvs("adb"))
        self.assertNotIn("monkey", calls)
        self.assertIn("app_process /system/bin HoldKey 23 400 800", calls)

    def test_pushes_the_program_it_ships_and_takes_it_away_again(self):
        self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER")
        pushes = [argv for argv in self.sandbox.argvs("adb") if "push" in argv]
        self.assertEqual(len(pushes), 1)
        self.assertEqual(Path(pushes[0][pushes[0].index("push") + 1]).read_bytes(),
                         (LIB / "HoldKey.dex").read_bytes())
        self.assertEqual(self.sandbox.device_state(self.serial)["files"], {})

    def test_the_shipped_program_is_a_dex_of_HoldKey(self):
        dex = (LIB / "HoldKey.dex").read_bytes()
        self.assertEqual(dex[:4], b"dex\n")
        self.assertIn(b"HoldKey", dex)
        self.assertIn(b"Events injected: 3", dex)   # what tv-remote.sh reads as success
        self.assertIn(b"HoldKey", (LIB / "HoldKey.java").read_bytes())

    def test_holds_the_key_for_twice_the_devices_long_press_timeout(self):
        result = self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER")
        self.assertSucceeded(result)
        self.assertEqual(result.out, "")
        self.assertEqual(self.long_presses(self.serial), [(23, 400, 800)])
        self.assertEqual(self.presses(), [], "no `input keyevent`: it can't hold before API 30")

    def test_holds_500_ms_twice_when_the_timeout_is_unset(self):
        self.set_long_press_timeout("null")   # never set on the device: Android's default applies
        serial = self.sandbox.start_emulator("phone")
        self.assertSucceeded(self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER", serial))
        self.assertEqual(self.long_presses(serial), [(23, 500, 1000)])

    def test_key_names_and_codes(self):
        for key, code in [("DPAD_CENTER", 23), ("KEYCODE_BACK", 4), ("HOME", 3), ("ENTER", 66),
                          ("SEARCH", 84), ("MEDIA_PLAY_PAUSE", 85), ("165", 165)]:
            with self.subTest(key=key):
                self.assertSucceeded(self.sandbox.run("tv-remote.sh", "--long-press", key))
                self.assertEqual(self.long_presses(self.serial)[-1], (code, 400, 800))

    def test_an_unknown_key_names_the_known_ones_and_sends_nothing(self):
        result = self.sandbox.run("tv-remote.sh", "--long-press", "OK")
        self.assertFailed(result, "unknown key 'OK'", code=2)
        self.assertIn("DPAD_CENTER", result.err)
        self.assertEqual(self.sandbox.calls("adb"), [])

    def test_needs_a_key(self):
        self.assertFailed(self.sandbox.run("tv-remote.sh", "--long-press"), "Usage:", code=2)

    def test_the_serial_given(self):
        other = self.sandbox.start_emulator("phone")
        self.assertSucceeded(self.sandbox.run("tv-remote.sh", "--long-press", "BACK", other))
        self.assertEqual(self.long_presses(other), [(4, 400, 800)])
        self.assertEqual(self.long_presses(self.serial), [])

    def test_leaves_no_file_on_the_device_or_the_host(self):
        self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER")
        self.assertEqual(self.sandbox.device_state(self.serial)["files"], {})
        self.assertEqual(list(self.sandbox.tmp.glob("adt-*")), [])

    def test_changes_no_setting_of_the_device(self):
        # monkey locked the rotation and unlocked it again on exit, and the settings were put back.
        before = (self.sandbox.device_settings(self.serial), self.sandbox.system_settings(self.serial))
        self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER")
        self.assertEqual((self.sandbox.device_settings(self.serial), self.sandbox.system_settings(self.serial)), before)
        self.assertNotIn("put", " ".join(" ".join(argv) for argv in self.sandbox.argvs("adb")))

    def test_a_failed_run_is_an_error_with_its_message(self):
        # Before API 24 adb shell always exits 0, so failure is read from the program's output.
        self.sandbox.set_behavior(hold_key_output="Error: Unable to open class path")
        result = self.sandbox.run("tv-remote.sh", "--long-press", "DPAD_CENTER")
        self.assertFailed(result, "the long press failed on emulator-5554")
        self.assertIn("Error: Unable to open class path", result.err)
        self.assertEqual(self.sandbox.device_state(self.serial)["files"], {})

    def test_l_then_a_key_holds_that_key_in_the_remote(self):
        result = self.sandbox.run("tv-remote.sh", input="l\nl\x1b[Blhh")
        self.assertSucceeded(result)
        self.assertEqual(self.long_presses(self.serial), [(23, 400, 800), (20, 400, 800), (3, 400, 800)])
        self.assertEqual([key for _, key in self.presses()], ["HOME"], "only the key after l")

    def test_another_key_after_l_cancels_it(self):
        self.sandbox.run("tv-remote.sh", input="lxh")
        self.assertEqual(self.long_presses(self.serial), [])
        self.assertEqual([key for _, key in self.presses()], ["HOME"])

    def test_the_remote_keeps_going_after_a_failed_long_press(self):
        self.sandbox.set_behavior(hold_key_output="Error: Unable to open class path")
        result = self.sandbox.run("tv-remote.sh", input="l\nh")
        self.assertSucceeded(result)
        self.assertIn("the long press failed", result.err)
        self.assertEqual([key for _, key in self.presses()], ["HOME"])


class ChoosesTheEmulator(RemoteTestCase):
    def test_the_only_running_emulator(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("tv-remote.sh", input="h")
        self.assertIn(f"Controlling {serial}", result.out)
        self.assertEqual(self.presses(), [(serial, "HOME")])

    def test_ignores_physical_devices(self):
        self.sandbox.connect_device("R58M123ABC")
        serial = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("tv-remote.sh", input="h")
        self.assertEqual(self.presses(), [(serial, "HOME")])

    def test_android_serial_picks_one_of_several(self):
        self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("tv-remote.sh", input="h", env={"ANDROID_SERIAL": tv})
        self.assertEqual(self.presses(), [(tv, "HOME")])

    def test_argument_wins_over_android_serial(self):
        phone = self.sandbox.start_emulator("phone")
        tv = self.sandbox.start_emulator("tv_api25")
        self.sandbox.run("tv-remote.sh", tv, input="h", env={"ANDROID_SERIAL": phone})
        self.assertEqual(self.presses(), [(tv, "HOME")])

    def test_several_emulators_without_a_choice(self):
        self.sandbox.start_emulator("phone")
        self.sandbox.start_emulator("tv_api25")
        result = self.sandbox.run("tv-remote.sh", input="h")
        self.assertFailed(result, "several emulators are running (emulator-5554 emulator-5556)")
        self.assertEqual(self.presses(), [])

    def test_no_emulator_running(self):
        self.sandbox.connect_device("R58M123ABC")
        self.assertFailed(self.sandbox.run("tv-remote.sh", input="h"), "no running emulator")

    def test_a_physical_device_when_named(self):
        self.sandbox.start_emulator("tv_api25")
        self.sandbox.connect_device("R58M123ABC")
        self.assertSucceeded(self.sandbox.run("tv-remote.sh", "R58M123ABC", input="h"))
        self.assertEqual(self.presses(), [("R58M123ABC", "HOME")])

    def test_unknown_option_prints_usage(self):
        self.assertFailed(self.sandbox.run("tv-remote.sh", "--fast"), "Usage:", code=2)
