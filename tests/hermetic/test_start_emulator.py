"""start-emulator.sh: choosing the AVD, booting it, finding its serial and reporting problems."""
import os
import subprocess
import time

from support.sandbox import Result, ScriptTestCase

# The fake device's home app (fake_tools.py's default), and Settings' FallbackHome.
HOME = "com.google.android.tvlauncher/.MainActivity"
HOME_WINDOW = "com.google.android.tvlauncher/com.google.android.tvlauncher.MainActivity"
FALLBACK_HOME = "com.android.tv.settings/.system.FallbackHome"
FALLBACK_HOME_WINDOW = "com.android.tv.settings/com.android.tv.settings.system.FallbackHome"


class EmulatorTestCase(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.install_sdk()

    def start(self, *args, env=None):
        return self.sandbox.run("start-emulator.sh", *args, env=env)

    def start_in_background(self, *args, env=None):
        """Starts the script as start() does, but returns its process at once, for a test that
        acts while it runs. Its output goes to files, which output() reads; it's killed at the
        end of the test."""
        self.stdout, self.stderr = self.sandbox.root / "stdout", self.sandbox.root / "stderr"
        with open(self.stdout, "w") as out, open(self.stderr, "w") as err:
            script = subprocess.Popen([str(self.sandbox.tools / "bin" / "start-emulator.sh"), *args],
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      env=self.sandbox.env(**(env or {})), cwd=self.sandbox.project)
        self.addCleanup(script.wait)
        self.addCleanup(script.kill)
        return script

    def output(self, script):
        """What a script from start_in_background() has printed so far."""
        return Result(script.returncode, self.stdout.read_text(), self.stderr.read_text())

    def launched(self):
        """Argument lists the emulator was started with (not -list-avds queries)."""
        return [argv for argv in self.sandbox.argvs("emulator") if argv[:1] == ["-avd"]]


class ChoosesTheAvd(EmulatorTestCase):
    def booted_avd(self, result):
        self.assertSucceeded(result)
        [argv] = self.launched()
        return argv[1]

    def test_tv_api25_by_default(self):
        self.sandbox.add_avd("other_tv")
        self.sandbox.add_avd("tv_api25")
        self.assertEqual(self.booted_avd(self.start()), "tv_api25")

    def test_the_name_given(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("my_tv")
        self.assertEqual(self.booted_avd(self.start("my_tv")), "my_tv")

    def test_adt_avd_when_no_name_is_given(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("den_tv")
        self.assertEqual(self.booted_avd(self.start(env={"ADT_AVD": "den_tv"})), "den_tv")

    def test_the_only_tv_avd_when_there_is_no_tv_api25(self):
        self.sandbox.add_avd("pixel", tv=False)
        self.sandbox.add_avd("living_room")
        self.sandbox.add_avd("tablet", tv=False)
        self.assertEqual(self.booted_avd(self.start()), "living_room")

    def test_a_google_tv_avd_counts_as_a_tv_avd(self):
        self.sandbox.add_avd("pixel", tv=False)
        self.sandbox.add_avd("gtv_api36", tag="google-tv")
        self.assertEqual(self.booted_avd(self.start()), "gtv_api36")

    def test_leading_flags_go_to_the_emulator_and_keep_the_default_avd(self):
        self.sandbox.add_avd("tv_api25")
        self.assertSucceeded(self.start("-wipe-data", "-no-snapshot-load"))
        [argv] = self.launched()
        self.assertEqual(argv[1], "tv_api25")
        self.assertIn("-wipe-data", argv)
        self.assertEqual(argv.count("-no-snapshot-load"), 1, "not added a second time")

    def test_quick_before_or_after_the_name(self):
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("my_tv")
        for args in (["--quick", "my_tv"], ["my_tv", "--quick", "-no-window"]):
            with self.subTest(args):
                self.assertSucceeded(self.start(*args))
                argv = self.launched()[-1]
                self.assertEqual(argv[1], "my_tv")
                self.assertNotIn("--quick", argv)
                self.sandbox.stop_emulators()

    def test_refuses_to_guess_between_several_tv_avds(self):
        self.sandbox.add_avd("tv_a")
        self.sandbox.add_avd("tv_b")
        result = self.start()
        self.assertFailed(result, "can't tell which AVD to use")
        self.assertIn("tv_a tv_b", result.output)
        self.assertEqual(self.launched(), [])

    def test_unknown_name_lists_the_avds(self):
        self.sandbox.add_avd("tv_api25")
        result = self.start("tv_typo")
        self.assertFailed(result, "no AVD named 'tv_typo'")
        self.assertIn("AVDs found: tv_api25", result.output)

    def test_no_avds_suggests_creating_one(self):
        result = self.sandbox.run("start-emulator.sh", how="relative")
        self.assertFailed(result, "AVDs found: none")
        self.assertIn("create one with\n../tools/bin/create-avd.sh", result.output)

    def test_avds_in_a_custom_avd_home(self):
        home = self.sandbox.root / "avds"
        self.sandbox.add_avd("tv_api25", home=home)
        result = self.start(env={"ANDROID_AVD_HOME": str(home)})
        self.assertEqual(self.booted_avd(result), "tv_api25")


class Boots(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_returns_only_once_android_has_booted(self):
        self.sandbox.set_behavior(boot_polls=2)
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        polls = [a for a in self.sandbox.argvs("adb") if "sys.boot_completed" in a]
        self.assertEqual(len(polls), 3, "polled until the third answer said booted")
        self.assertEqual(self.sandbox.running(), {"emulator-5554": "tv_api25"},
                         "the emulator keeps running after the script exits")

    def test_software_rendering_without_audio_or_boot_animation(self):
        self.assertSucceeded(self.start())
        [argv] = self.launched()
        self.assertEqual(argv[argv.index("-gpu") + 1], "swiftshader_indirect")
        self.assertIn("-no-audio", argv)
        self.assertIn("-no-boot-anim", argv)

    def test_own_gpu_flag_replaces_the_default(self):
        self.assertSucceeded(self.start("tv_api25", "-gpu", "host"))
        [argv] = self.launched()
        self.assertEqual(argv.count("-gpu"), 1)
        self.assertEqual(argv[argv.index("-gpu") + 1], "host")

    def test_logs_to_tmpdir(self):
        result = self.start()
        log = self.sandbox.tmp / "emulator-tv_api25.log"
        self.assertIn(str(log), result.err)
        self.assertIn("Android emulator version", log.read_text())

    def test_already_running_avd_is_reported_not_started_again(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn(f"already running as {serial}", result.err)
        self.assertEqual(self.launched(), [])

    def test_crash_during_boot_is_reported_with_the_log(self):
        self.sandbox.set_behavior(emulator_crash="unknown option: -bogus")
        result = self.start("-bogus")
        self.assertFailed(result, "the emulator exited")   # not waiting for the boot timeout
        self.assertIn("unknown option: -bogus", result.output, "the log's last lines are shown")


class ColdOrQuickBoot(EmulatorTestCase):
    """Cold boot by default; --quick boots from the Quick Boot snapshot, whose restored adb
    connection can stay offline, so --quick watches for that (ADT_OFFLINE_TIMEOUT, 30 s)."""

    FAST = {"ADT_OFFLINE_TIMEOUT": "1"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def reconnects(self):
        return [argv for argv in self.sandbox.argvs("adb") if argv[:1] == ["reconnect"]]

    def test_cold_boot_by_default(self):
        result = self.start()
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertIn("-no-snapshot-load", argv)
        self.assertIn("cold boot", result.err)

    def test_quick_boots_from_the_snapshot(self):
        result = self.start("--quick")
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertNotIn("-no-snapshot-load", argv)
        self.assertNotIn("--quick", argv, "the emulator doesn't know --quick")
        self.assertIn("Quick Boot", result.err)

    def test_quick_reconnects_adb_when_the_restored_emulator_stays_offline(self):
        self.sandbox.set_behavior(adb_offline="until_reconnect")
        result = self.start("--quick", env=self.FAST)
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        self.assertEqual(self.reconnects(), [["reconnect", "offline"]])

    def test_quick_gives_up_when_reconnecting_does_not_help(self):
        self.sandbox.set_behavior(adb_offline="forever")
        # No time check: with the default 30 s, giving up would take longer than sandbox.run
        # lets the script run; with ADT_OFFLINE_TIMEOUT=1 it takes a few 2 s boot polls, and
        # more on a busy machine.
        result = self.start("--quick", env=self.FAST)
        self.assertFailed(result, "adb can't reach it")
        self.assertIn("stop-emulator.sh emulator-5554 && ", result.output)
        self.assertIn("without --quick", result.output)
        self.assertEqual(len(self.reconnects()), 1, "reconnects once, then gives up")

    def test_a_slow_cold_boot_is_waited_for_without_reconnecting(self):
        # adbd isn't up yet early in a cold boot, so "offline" for a while is normal there.
        self.sandbox.set_behavior(adb_offline=2)
        result = self.start(env=self.FAST)
        self.assertSucceeded(result)
        self.assertEqual(self.reconnects(), [])

    def test_quick_does_not_reconnect_a_briefly_offline_emulator(self):
        self.sandbox.set_behavior(adb_offline=1)
        self.assertSucceeded(self.start("--quick"))
        self.assertEqual(self.reconnects(), [])


class BootTimeout(EmulatorTestCase):
    """An emulator that runs but never boots must not keep the script (or a CI job) waiting
    forever: after ADT_BOOT_TIMEOUT seconds it's stopped and the script fails, even when the adb
    calls that check the boot hang (each gets ADT_ADB_TIMEOUT seconds, 15 by default)."""

    FAST = {"ADT_BOOT_TIMEOUT": "2"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def assertGaveUp(self, result, started):
        self.assertFailed(result, "didn't finish booting within 2 s")
        self.assertIn("Android emulator version", result.output, "the log's last lines are shown")
        self.assertIn("ADT_BOOT_TIMEOUT", result.output)
        self.assertIn("start-emulator.sh tv_api25 -wipe-data", result.output)
        self.assertEqual(self.sandbox.running(), {}, "the half-booted emulator is stopped")
        # What the output can't show: a hung adb call ended after ADT_ADB_TIMEOUT, not 15 s.
        self.assertLess(time.monotonic() - started, 15)

    def test_stops_an_emulator_that_never_finishes_booting(self):
        self.sandbox.set_behavior(boot_polls=10**9)
        started = time.monotonic()
        self.assertGaveUp(self.start(env=self.FAST), started)

    def test_stops_an_emulator_that_adb_never_sees(self):
        self.sandbox.set_behavior(emulator_hidden=True)
        started = time.monotonic()
        self.assertGaveUp(self.start(env=self.FAST), started)

    def test_stops_an_emulator_whose_boot_check_never_answers(self):
        self.sandbox.set_behavior(adb_hangs={"getprop": 10**9})
        started = time.monotonic()
        self.assertGaveUp(self.start(env={**self.FAST, "ADT_ADB_TIMEOUT": "1"}), started)

    def test_stops_an_emulator_whose_console_never_answers(self):
        self.sandbox.set_behavior(adb_hangs={"avd_name": 10**9})
        started = time.monotonic()
        self.assertGaveUp(self.start(env={**self.FAST, "ADT_ADB_TIMEOUT": "1"}), started)

    def test_asks_again_after_a_boot_check_that_hung(self):
        self.sandbox.set_behavior(adb_hangs={"getprop": 1})
        result = self.start(env={"ADT_ADB_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5554\n")

    def test_a_retry_after_a_timeout_boots_again(self):
        # The stopped emulator must be gone, or the retry would report it as already running.
        self.sandbox.set_behavior(boot_polls=10**9)
        self.assertFailed(self.start(env=self.FAST), "didn't finish booting")
        self.sandbox.set_behavior(boot_polls=0)
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5554", result.err)
        self.assertEqual(len(self.launched()), 2)

    def test_zero_means_no_limit(self):
        self.sandbox.set_behavior(boot_polls=2)
        self.assertSucceeded(self.start(env={"ADT_BOOT_TIMEOUT": "0"}))

    def test_must_be_a_number_of_seconds(self):
        for value in ("10m", "-1", "1.5"):
            with self.subTest(value=value):
                self.assertFailed(self.start(env={"ADT_BOOT_TIMEOUT": value}),
                                  "ADT_BOOT_TIMEOUT must be a number of seconds")
        self.assertEqual(self.launched(), [], "nothing is started with a bad timeout")


class BootProgress(EmulatorTestCase):
    """A boot can take minutes with nothing to show, which in a CI log looks like a hang: every
    ADT_PROGRESS_INTERVAL seconds (60 by default) a line on stderr says it's still booting."""

    FAST = {"ADT_PROGRESS_INTERVAL": "1"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_says_it_is_still_booting_while_android_boots(self):
        self.sandbox.set_behavior(boot_polls=2)
        result = self.start(env=self.FAST)
        self.assertSucceeded(result)
        self.assertRegex(result.err, r"'tv_api25' is still booting \(\d+ s so far; the limit is 900 s\)")
        self.assertEqual(result.out, "emulator-5554\n", "stdout holds only the serial")

    def test_says_it_is_still_booting_before_adb_sees_it(self):
        self.sandbox.set_behavior(emulator_hidden=True)
        script = self.start_in_background(env=self.FAST)
        self.wait_until(lambda: "still booting" in self.output(script).err or script.poll() is not None)
        self.assertIn("'tv_api25' is still booting (", self.output(script).err)

    def test_names_no_limit_when_there_is_none(self):
        self.sandbox.set_behavior(boot_polls=2)
        result = self.start(env={**self.FAST, "ADT_BOOT_TIMEOUT": "0"})
        self.assertSucceeded(result)
        self.assertRegex(result.err, r"'tv_api25' is still booting \(\d+ s so far\)\.\.\.")

    def test_quiet_when_the_boot_is_quick(self):
        self.sandbox.set_behavior(boot_polls=2)
        result = self.start()
        self.assertSucceeded(result)
        self.assertNotIn("still booting", result.err)


class MarksTheTvSetupComplete(EmulatorTestCase):
    """Android TV 8.0 and 8.1 (API 26, 27) ignore the Home key until tv_user_setup_complete is
    set, which their emulator images never do: after the boot, the script sets it, then waits
    until Android has saved it to disk (read as root) and commits that (sync), so a kill can't
    lose it, for at most ADT_SAVE_TIMEOUT seconds; then, or if it can't look, it only warns."""

    FLAG = "tv_user_setup_complete"

    def add_tv_avd(self, level, tag="android-tv"):
        name = f"{'gtv' if tag == 'google-tv' else 'tv'}_api{level}"
        self.sandbox.add_avd(name, image=f"system-images/android-{level}/{tag}/x86/", tag=tag)
        return name

    def settings_puts(self):
        return [a for a in self.sandbox.argvs("adb") if "settings" in a and "put" in a]

    def saved_looks(self):
        return [a for a in self.sandbox.argvs("adb") if any("settings_secure.xml" in x for x in a)]

    def test_sets_it_on_android_tv_8_0_and_8_1(self):
        for level in (26, 27):
            with self.subTest(level=level):
                avd = self.add_tv_avd(level)
                result = self.start(avd)
                self.assertSucceeded(result)
                serial = result.out.strip()
                self.assertEqual(self.sandbox.device_settings(serial).get(self.FLAG), "1")
                self.assertIn("so the Home key works", result.err)
                self.assertIn("Waiting until Android has saved it", result.err)
                self.assertNotIn("Warning", result.err)
                self.assertEqual(result.out, f"{serial}\n", "stdout holds only the serial")
                self.sandbox.stop_emulators()

    def test_waits_until_android_has_saved_it(self):
        # Android writes it a moment after the change: the script looks until it's on disk.
        self.sandbox.set_behavior(settings_unsaved_looks=3)
        result = self.start(self.add_tv_avd(26))
        self.assertSucceeded(result)
        self.assertEqual(len(self.saved_looks()), 4)
        self.assertNotIn("Warning", result.err)
        calls = self.sandbox.argvs("adb")
        self.assertLess(calls.index(self.settings_puts()[0]), calls.index(self.saved_looks()[0]),
                        "it looks only after setting it")

    def test_warns_when_android_does_not_save_it_in_time(self):
        self.sandbox.set_behavior(settings_unsaved_looks="forever")
        started = time.monotonic()
        result = self.start(self.add_tv_avd(27), env={"ADT_SAVE_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertGreaterEqual(time.monotonic() - started, 1)
        self.assertIn("couldn't see Android save tv_user_setup_complete (it wasn't saved within 1 s)",
                      result.err)
        self.assertEqual(result.out, "emulator-5554\n")

    def test_warns_at_once_when_it_cannot_look(self):
        self.sandbox.set_behavior(su_error="/system/bin/sh: su: not found")
        result = self.start(self.add_tv_avd(26))
        self.assertSucceeded(result)
        self.assertEqual(len(self.saved_looks()), 1)
        self.assertIn("couldn't see Android save tv_user_setup_complete "
                      "(/system/bin/sh: su: not found)", result.err)
        self.assertEqual(result.out, "emulator-5554\n")

    def test_rejects_a_save_timeout_that_is_not_a_number(self):
        result = self.start(self.add_tv_avd(26), env={"ADT_SAVE_TIMEOUT": "soon"})
        self.assertFailed(result, "ADT_SAVE_TIMEOUT must be a number of seconds, not 'soon'", code=1)

    def test_leaves_the_other_images_alone(self):
        for level, tag in ((25, "android-tv"), (28, "android-tv"), (36, "android-tv"),
                           (30, "google-tv")):
            with self.subTest(level=level, tag=tag):
                avd = self.add_tv_avd(level, tag)
                result = self.start(avd)
                self.assertSucceeded(result)
                self.assertEqual(self.sandbox.device_settings(result.out.strip()), {})
                self.assertNotIn("Home key", result.err)
                self.assertNotIn("Waiting", result.err)
                self.sandbox.stop_emulators()
        self.assertEqual(self.settings_puts(), [])
        self.assertEqual(self.saved_looks(), [])

    def test_leaves_it_alone_when_it_is_already_set(self):
        self.sandbox.set_behavior(device_settings={self.FLAG: "1"})
        result = self.start(self.add_tv_avd(27))
        self.assertSucceeded(result)
        self.assertEqual(self.settings_puts(), [])
        self.assertEqual(self.saved_looks(), [])
        self.assertNotIn("Home key", result.err)
        self.assertNotIn("Waiting", result.err)

    def test_sets_it_on_an_emulator_started_elsewhere(self):
        avd = self.add_tv_avd(26)
        serial = self.sandbox.start_emulator(avd)
        result = self.start(avd)
        self.assertSucceeded(result)
        self.assertEqual(self.sandbox.device_settings(serial).get(self.FLAG), "1")
        self.assertEqual(len(self.saved_looks()), 1)

    def test_only_warns_when_it_cannot_set_it(self):
        self.sandbox.set_behavior(settings_put_error="Error: permission denied")
        result = self.start(self.add_tv_avd(27))
        self.assertSucceeded(result)
        self.assertIn("couldn't set tv_user_setup_complete", result.err)
        self.assertNotIn("Waiting", result.err)
        self.assertEqual(self.saved_looks(), [])
        self.assertEqual(result.out, "emulator-5554\n")


class AlreadyRunning(EmulatorTestCase):
    """An AVD that's already running isn't started again, but it may still be booting (another
    call or CI step started it): the script returns only once Android has booted, as always."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def boot_polls(self):
        return [a for a in self.sandbox.argvs("adb") if "sys.boot_completed" in a]

    def test_waits_until_a_still_booting_emulator_has_booted(self):
        self.sandbox.set_behavior(boot_polls=2)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{serial}\n")
        self.assertIn(f"already running as {serial}", result.err)
        self.assertEqual(len(self.boot_polls()), 3, "polled until the third answer said booted")
        self.assertEqual(self.launched(), [])

    def test_returns_at_once_when_it_has_booted(self):
        self.sandbox.start_emulator("tv_api25")
        self.assertSucceeded(self.start())
        self.assertEqual(len(self.boot_polls()), 1)

    def test_gives_up_on_one_that_never_boots_but_leaves_it_running(self):
        # It wasn't started here, so it's not this script's to stop.
        self.sandbox.set_behavior(boot_polls=10**9)
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start(env={"ADT_BOOT_TIMEOUT": "2"})
        self.assertFailed(result, "didn't finish booting within 2 s")
        self.assertIn("wasn't started by this script", result.output)
        self.assertEqual(result.out, "")
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_stops_waiting_when_it_is_stopped_meanwhile(self):
        self.sandbox.set_behavior(boot_polls=10**9)
        self.sandbox.start_emulator("tv_api25")
        script = self.start_in_background()
        # Stopped once the script waits for its boot: any earlier, the script wouldn't find it
        # and would start the AVD itself.
        self.wait_until(lambda: self.boot_polls() or self.launched() or script.poll() is not None)
        self.assertEqual(self.launched(), [], "started the AVD instead of waiting for it")
        self.assertTrue(self.boot_polls(), f"never waited for the boot:\n{self.output(script).output}")
        self.sandbox.stop_emulators()
        try:
            script.wait(timeout=20)   # the boot timeout is 900 s
        except subprocess.TimeoutExpired:
            self.fail("kept waiting for the stopped emulator")
        self.assertFailed(self.output(script), "stopped before it finished booting")


class WaitsForHome(EmulatorTestCase):
    """--wait-for-home: after the boot, the script also waits until the device has settled: the
    home app's activity at the top, resumed and idle, focused, with its own window focused, and
    from API 24 on a HOME intent no longer resolving to FallbackHome, at two looks in a row; for at
    most ADT_HOME_TIMEOUT seconds. Another app's screen that keeps the focus gets Back after
    ADT_BACK_AFTER seconds (10 by default; 1 here). The fake device shows what `front` and
    `home_resolves` list, one item per look; test_lib.py's WaitForHome checks the decision on
    dumps the emulators produced."""

    SETTINGS = "com.android.tv.settings/.MainSettings"
    SETTINGS_WINDOW = "com.android.tv.settings/com.android.tv.settings.MainSettings"
    USB = ["com.android.tv.settings/.device.storage.NewStorageActivity",   # "USB drive connected"
           "com.android.tv.settings/com.android.tv.settings.device.storage.NewStorageActivity"]
    SENSOR_PRIVACY = ["com.android.systemui/.sensorprivacy.television.TvSensorPrivacyChangedActivity",
                      "com.android.systemui/com.android.systemui.sensorprivacy.television."
                      "TvSensorPrivacyChangedActivity"]   # shown for a moment at boot, API 34
    CHOOSER = "android/com.android.internal.app.ResolverActivity"
    SOON = {"ADT_HOME_TIMEOUT": "10"}   # to fail rather than wait, if it doesn't press Back
    NO_FOCUS = [HOME, None]   # the home app in front, but no window has the focus
    FAST = {"ADT_HOME_TIMEOUT": "2"}

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def start(self, *args, env=None):
        return super().start(*args, env={"ADT_BACK_AFTER": "1", **(env or {})})

    def looks(self):
        return [a for a in self.sandbox.argvs("adb") if a[-2:] == ["dumpsys", "window"]]

    def backs(self):
        return [a for a in self.sandbox.argvs("adb") if a[-3:] == ["input", "keyevent", "BACK"]]

    def test_waits_until_a_window_of_the_home_app_has_the_focus(self):
        self.sandbox.set_behavior(front=[self.NO_FOCUS] * 3 + [[HOME, HOME_WINDOW]])
        result = self.start("--wait-for-home")
        self.assertSucceeded(result)
        self.assertGreater(len(self.looks()), 3, "returned before the focus came")
        self.assertIn("The home app (com.google.android.tvlauncher) is in front.", result.err)
        self.assertEqual(result.out, "emulator-5554\n", "stdout holds only the serial")
        [argv] = self.launched()
        self.assertNotIn("--wait-for-home", argv, "the emulator doesn't know --wait-for-home")
        self.assertEqual(self.backs(), [], "no Back without a need")

    def test_waits_while_home_resolves_to_fallback_home(self):
        # Until the user is unlocked, FallbackHome is in front, with the focus, and a HOME intent
        # resolves to it: it looks like a home app that has settled.
        self.sandbox.set_behavior(home_resolves=[FALLBACK_HOME] * 3 + [HOME],
                                  front=[[FALLBACK_HOME, FALLBACK_HOME_WINDOW]] * 3
                                  + [[HOME, HOME_WINDOW]])
        result = self.start("--wait-for-home")
        self.assertSucceeded(result)
        self.assertIn("(com.google.android.tvlauncher) is in front", result.err,
                      "took FallbackHome for the home app")

    def test_waits_until_the_home_app_is_in_front_at_two_looks_in_a_row(self):
        flapping = [[HOME, HOME_WINDOW], self.NO_FOCUS] * 2
        self.sandbox.set_behavior(front=flapping + [[HOME, HOME_WINDOW]])
        self.assertSucceeded(self.start("--wait-for-home"))
        self.assertEqual(len(self.looks()), len(flapping) + 2, "returned while it still changed")

    def test_presses_back_when_another_screen_keeps_the_focus(self):
        # A new AVD's first boot shows "USB drive connected" over the home app on API 23 and 29.
        self.sandbox.set_behavior(front=[self.USB, "BACK", [HOME, HOME_WINDOW]])
        result = self.start("--wait-for-home", env=self.SOON)
        self.assertSucceeded(result)
        self.assertEqual(len(self.backs()), 1)
        self.assertIn("(com.google.android.tvlauncher) is in front", result.err)

    def test_presses_back_on_one_already_running_whose_boot_it_waited_for(self):
        # Started a moment ago, e.g. by another CI step: nobody uses it yet.
        self.sandbox.set_behavior(front=[self.USB, "BACK", [HOME, HOME_WINDOW]], boot_polls=1)
        self.sandbox.start_emulator("tv_api25")
        self.assertSucceeded(self.start("--wait-for-home", env=self.SOON))
        self.assertEqual(len(self.backs()), 1)

    def test_presses_back_twice_at_most(self):
        self.sandbox.set_behavior(front=[self.USB])
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "8"})
        self.assertFailed(result, f"In front: activity {self.USB[0]}, focused window {self.USB[1]}")
        self.assertEqual(len(self.backs()), 2)

    def test_no_back_before_the_user_is_unlocked(self):
        # Another app's window over FallbackHome.
        self.sandbox.set_behavior(home_resolves=[FALLBACK_HOME] * 4 + [HOME],
                                  front=[self.SENSOR_PRIVACY] * 4 + [[HOME, HOME_WINDOW]])
        self.assertSucceeded(self.start("--wait-for-home"))
        self.assertEqual(self.backs(), [])

    def test_fails_at_once_when_no_home_app_is_enabled(self):
        # A stock launcher left disabled: once the user is unlocked, a HOME intent still finds only
        # FallbackHome, which stays in front; no home app can come. The state's format differs
        # (dumpsys user: {0=3} up to API 32, [0=RUNNING_UNLOCKED] from 33 on).
        for level in (24, 30, 36):
            with self.subTest(api_level=level):
                self.sandbox.set_behavior(api_level=level, home_resolves=[FALLBACK_HOME],
                                          user_unlocked=True,
                                          front=[[FALLBACK_HOME, FALLBACK_HOME_WINDOW]])
                looks_before = len(self.looks())
                result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "60"})
                self.assertFailed(result, "'tv_api25' booted, but no home app is enabled (a disabled "
                                          "stock launcher?): the user is unlocked, and nothing but "
                                          "Settings' FallbackHome handles a HOME intent,\nso it was "
                                          "stopped.")
                self.assertIn(f"In front: activity {FALLBACK_HOME}, focused window "
                              f"{FALLBACK_HOME_WINDOW}; a HOME intent resolves to {FALLBACK_HOME};",
                              result.output)
                self.assertIn("enable its home app, e.g. its stock launcher\n(adb shell pm list "
                              "packages -d lists the disabled apps", result.output)
                self.assertEqual(len(self.looks()) - looks_before, 2, "not at two looks in a row")
                self.assertEqual(self.sandbox.running(), {}, "the emulator it started is stopped")

    def test_leaves_one_that_had_booted_running_when_no_home_app_is_enabled(self):
        self.sandbox.set_behavior(home_resolves=[FALLBACK_HOME], user_unlocked=True,
                                  front=[[FALLBACK_HOME, FALLBACK_HOME_WINDOW]])
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "60"})
        self.assertFailed(result, f"'tv_api25' ({serial}) booted, but no home app is enabled")
        self.assertIn(f"left running.\nEnable its home app, e.g. its stock launcher, then try "
                      f"again\n(adb -s {serial} shell pm list packages -d lists the disabled apps; "
                      f"adb -s {serial} shell pm enable <package>).", result.output)
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_fallback_home_alone_is_no_sign_while_the_user_is_locked(self):
        # Until the user is unlocked, Android lists only FallbackHome for a HOME intent, even with
        # a home app enabled: that's the way to the home screen, which gets the long limit.
        for level in (24, 30, 36):
            with self.subTest(api_level=level):
                self.sandbox.set_behavior(api_level=level, home_resolves=[FALLBACK_HOME],
                                          front=[[FALLBACK_HOME, FALLBACK_HOME_WINDOW]])
                result = self.start("--wait-for-home", env=self.FAST)
                self.assertFailed(result, "home app wasn't in front with the focus within 2 s")
                self.assertNotIn("no home app is enabled", result.output)

    def test_a_home_app_missing_from_one_look_is_no_sign(self):
        # An app being updated is missing from the query for a moment.
        self.sandbox.set_behavior(home_resolves=[FALLBACK_HOME, HOME], user_unlocked=True,
                                  home_activities=[[FALLBACK_HOME], [HOME, FALLBACK_HOME]],
                                  front=[[FALLBACK_HOME, FALLBACK_HOME_WINDOW], [HOME, HOME_WINDOW]])
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "60"})
        self.assertSucceeded(result)
        self.assertIn("The home app (com.google.android.tvlauncher) is in front.", result.err)

    def test_waits_while_another_window_has_the_focus(self):
        for front in ([HOME, "Application Not Responding: com.example.tv"],   # a dialog
                      ["com.android.systemui/.SomeActivity",
                       "com.android.systemui/com.android.systemui.SomeActivity"]):
            with self.subTest(front=front):
                self.sandbox.set_behavior(front=[front])
                result = self.start("--wait-for-home", env=self.FAST)
                self.assertFailed(result, "home app wasn't in front with the focus within 2 s")
                self.assertIn(f"In front: activity {front[0]}, focused window {front[1]}; a HOME "
                              f"intent resolves to {HOME}", result.output)

    def test_gives_up_and_stops_the_emulator_it_started(self):
        self.sandbox.set_behavior(front=[self.NO_FOCUS])
        result = self.start("--wait-for-home", env=self.FAST)
        self.assertFailed(result, "home app wasn't in front with the focus within 2 s")
        self.assertIn(f"In front: activity {HOME}, focused window none;", result.output)
        self.assertIn("so it was stopped", result.output)
        self.assertIn("ADT_HOME_TIMEOUT", result.output)
        self.assertEqual(result.out, "")
        self.assertEqual(self.sandbox.running(), {}, "the unsettled emulator is stopped")

    def test_leaves_one_that_had_booted_as_it_is(self):
        # Someone may be using it: no Back, and it keeps running.
        self.sandbox.set_behavior(front=[[self.SETTINGS, self.SETTINGS_WINDOW]])
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "4"})
        self.assertFailed(result, "wasn't started by this script, so it's left running")
        self.assertIn(f"In front: activity {self.SETTINGS}", result.output)
        self.assertEqual(self.backs(), [])
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})

    def test_fails_soon_when_another_apps_screen_keeps_the_focus_on_one_that_had_booted(self):
        # An app left open on a device someone uses: no Back, and no wait for the long limit.
        self.sandbox.set_behavior(front=[[self.SETTINGS, self.SETTINGS_WINDOW]])
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "60",
                                                    "ADT_OTHER_APP_TIMEOUT": "2"})
        self.assertFailed(result, f"'tv_api25' ({serial}) booted, but a screen of another app than "
                                  "its home app has kept the focus for 2 s, so the home app can't "
                                  "come to the front.")
        self.assertIn(f"In front: activity {self.SETTINGS}, focused window {self.SETTINGS_WINDOW};",
                      result.output)
        self.assertIn("left running.\nNothing was pressed on it: close that screen", result.output)
        self.assertEqual(self.backs(), [])
        self.assertEqual(self.sandbox.running(), {serial: "tv_api25"})
        looks = [c for c in self.sandbox.calls("adb") if c["argv"][-2:] == ["dumpsys", "window"]]
        self.assertLess(looks[-1]["time"] - looks[0]["time"], 30, "waited for the long limit")

    def test_stops_the_emulator_it_started_when_another_screen_stays_after_back(self):
        self.sandbox.set_behavior(front=[self.USB])
        result = self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "60",
                                                    "ADT_OTHER_APP_TIMEOUT": "2"})
        self.assertFailed(result, "has kept the focus for 2 s, also after Back, so the home app "
                                  "can't come to the front,\nso it was stopped.")
        self.assertIn("To see that screen, boot it without --wait-for-home.", result.output)
        self.assertEqual(len(self.backs()), 2)
        self.assertEqual(self.sandbox.running(), {})

    def test_the_other_app_limit_must_be_a_number_of_seconds(self):
        result = self.start("--wait-for-home", env={"ADT_OTHER_APP_TIMEOUT": "1m"})
        self.assertFailed(result, "ADT_OTHER_APP_TIMEOUT must be a number of seconds (0: no such "
                                  "limit), not '1m'")
        self.assertEqual(self.launched(), [])

    def test_waits_up_to_600_s_by_default(self):
        self.sandbox.set_behavior(front=[[HOME, HOME_WINDOW]])
        result = self.start("--wait-for-home")
        self.assertSucceeded(result)
        self.assertIn("Waiting for the home app to be in front, with the focus (up to 600 s)...",
                      result.err)

    def test_zero_means_no_limit(self):
        self.sandbox.set_behavior(front=[self.NO_FOCUS] * 3 + [[HOME, HOME_WINDOW]])
        self.assertSucceeded(self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": "0"}))

    def test_before_api_24_the_home_app_is_the_one_a_home_intent_started(self):
        # No `cmd`, no FallbackHome; on API 22 with a second home app, Android's chooser.
        for home, window in ((HOME, HOME_WINDOW), (self.CHOOSER, self.CHOOSER)):
            with self.subTest(home=home):
                self.sandbox.set_behavior(api_level=22, home_resolves=[home], front=[[home, window]])
                result = self.start("--wait-for-home")
                self.assertSucceeded(result)
                self.assertIn(f"The home app ({home.split('/')[0]}) is in front", result.err)
                self.sandbox.stop_emulators()

    def test_before_api_24_another_app_in_front_is_not_home(self):
        self.sandbox.set_behavior(api_level=23, front=[[self.SETTINGS, self.SETTINGS_WINDOW]])
        result = self.start("--wait-for-home", env=self.FAST)
        self.assertFailed(result, "home app wasn't in front")
        self.assertIn(f"In front: activity {self.SETTINGS}, focused window {self.SETTINGS_WINDOW}; "
                      f"the top activity is {self.SETTINGS} in task 9, RESUMED, idle.", result.output)

    def test_before_api_24_presses_back_too(self):
        self.sandbox.set_behavior(api_level=23, front=[self.USB, "BACK", [HOME, HOME_WINDOW]])
        self.assertSucceeded(self.start("--wait-for-home", env=self.SOON))
        self.assertEqual(len(self.backs()), 1)

    def test_without_the_flag_it_does_not_wait(self):
        self.sandbox.set_behavior(front=[self.NO_FOCUS])
        result = self.start(env={"ADT_HOME_TIMEOUT": "not checked without the flag"})
        self.assertSucceeded(result)
        self.assertEqual(self.looks(), [])
        self.assertNotIn("home app", result.err)

    def test_the_limit_must_be_a_number_of_seconds(self):
        for value in ("5m", "-1"):
            with self.subTest(value=value):
                self.assertFailed(self.start("--wait-for-home", env={"ADT_HOME_TIMEOUT": value}),
                                  "ADT_HOME_TIMEOUT must be a number of seconds")
        self.assertEqual(self.launched(), [], "nothing is started with a bad limit")


class PrintsTheSerial(EmulatorTestCase):
    """stdout holds the serial and nothing else, so serial="$(start-emulator.sh)" works; every
    message goes to stderr."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_after_booting(self):
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5554\n")
        self.assertIn("booted as emulator-5554", result.err)

    def test_when_it_is_already_running(self):
        serial = self.sandbox.start_emulator("tv_api25")
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, f"{serial}\n")

    def test_with_other_devices_connected_and_on_wsl(self):
        # The ANDROID_SERIAL hint and the toolbar script's report are messages too.
        self.sandbox.add_avd("phone", tv=False)
        self.sandbox.start_emulator("phone")
        self.sandbox.connect_device("R58M123ABC")
        self.sandbox.wsl()
        result = self.start()
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5556\n")
        self.assertIn("export ANDROID_SERIAL=emulator-5556", result.err)
        self.assertIn("wslg-toolbar: toolbar hidden", result.err)

    def test_with_quick_after_a_reconnect(self):
        self.sandbox.set_behavior(adb_offline="until_reconnect")
        result = self.start("--quick", env={"ADT_OFFLINE_TIMEOUT": "1"})
        self.assertSucceeded(result)
        self.assertEqual(result.out, "emulator-5554\n")

    def test_nothing_on_failure(self):
        self.sandbox.set_behavior(emulator_crash="unknown option: -bogus")
        result = self.start("-bogus")
        self.assertFailed(result, "the emulator exited")
        self.assertEqual(result.out, "")


class WithoutADisplay(EmulatorTestCase):
    """Without $DISPLAY (a CI runner, SSH) the real emulator aborts with nothing in its log that
    says why, so -no-window is added."""

    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def test_runs_without_a_window(self):
        result = self.start(env={"DISPLAY": None})
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertEqual(argv.count("-no-window"), 1)
        self.assertIn("No $DISPLAY", result.err)

    def test_an_empty_display_counts_as_none(self):
        self.assertSucceeded(self.start(env={"DISPLAY": ""}))
        [argv] = self.launched()
        self.assertIn("-no-window", argv)

    def test_own_no_window_is_not_repeated(self):
        result = self.start("-no-window", env={"DISPLAY": None})
        self.assertSucceeded(result)
        [argv] = self.launched()
        self.assertEqual(argv.count("-no-window"), 1)
        self.assertNotIn("No $DISPLAY", result.err)

    def test_with_a_display_the_window_stays(self):
        self.assertSucceeded(self.start())
        [argv] = self.launched()
        self.assertNotIn("-no-window", argv)

    def test_no_toolbar_fix_on_wsl_without_a_window(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start(env={"DISPLAY": None}))
        self.assertEqual([a for a in self.sandbox.argvs("python3")], [])


class FindsItsOwnEmulator(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")
        self.sandbox.add_avd("phone", tv=False)

    def test_serial_of_its_avd_when_another_emulator_runs(self):
        self.sandbox.start_emulator("phone")             # takes emulator-5554
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("booted as emulator-5556", result.err)
        self.assertIn("export ANDROID_SERIAL=emulator-5556", result.err)

    def test_hint_when_a_physical_device_is_connected(self):
        self.sandbox.connect_device("R58M123ABC")
        result = self.start()
        self.assertSucceeded(result)
        self.assertIn("export ANDROID_SERIAL=emulator-5554", result.err)

    def test_no_hint_when_it_is_the_only_device(self):
        result = self.start()
        self.assertSucceeded(result)
        self.assertNotIn("ANDROID_SERIAL", result.err)

    def test_adb_calls_always_name_the_serial(self):
        self.sandbox.connect_device("R58M123ABC")
        self.assertSucceeded(self.start())
        for argv in self.sandbox.argvs("adb"):
            if argv[:1] != ["devices"]:
                self.assertEqual(argv[:1], ["-s"], f"adb call without -s: {argv}")


class Kvm(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")
        if os.geteuid() == 0:
            self.skipTest("root can write any file, so KVM access can't be taken away")

    def test_missing_kvm_device(self):
        self.sandbox.kvm.unlink()
        result = self.start()
        self.assertFailed(result, "doesn't exist: enable hardware virtualization")
        self.assertEqual(self.launched(), [])

    def test_no_access_and_not_in_the_kvm_group(self):
        self.sandbox.kvm.chmod(0o444)
        result = self.start()
        self.assertFailed(result, "sudo usermod -aG kvm tester")
        self.assertEqual(self.sandbox.argvs("sg"), [])
        self.assertEqual(self.launched(), [])

    def test_a_member_whose_name_contains_the_users_is_not_the_user(self):
        # The group line "kvm:x:<gid>:ci-bot" must not count user "ci" as a member: sg would
        # then ask for a group password.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(user="ci", kvm_group_members=["ci-bot"])
        result = self.start(env={"USER": "ci"})
        self.assertFailed(result, "sudo usermod -aG kvm ci")
        self.assertEqual(self.sandbox.argvs("sg"), [])

    def test_kvm_as_the_primary_group_reruns_through_sg(self):
        # A primary group's getent line doesn't list its users; the user database does.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_primary_group=True)
        self.start()
        [argv] = self.sandbox.argvs("sg")
        self.assertIn("_IN_SG_KVM=1", argv[2])

    def test_without_user_set(self):
        # Containers often don't set $USER; the user comes from `id -un` instead.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        result = self.start(env={"USER": None})
        self.assertNotIn("unbound variable", result.output)
        self.assertEqual(len(self.sandbox.argvs("sg")), 1)

    def test_the_device_belongs_to_another_group_than_kvm(self):
        # Seen on WSL: /dev/kvm is created before udev applies the kvm group, so it keeps a gid
        # no group owns and joining kvm changes nothing. Say so instead of suggesting usermod.
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_gid=4242, kvm_group_members=["tester"])
        result = self.start()
        self.assertFailed(result, "but the kvm group is")
        self.assertIn("sudo chgrp kvm", result.output)
        self.assertEqual(self.sandbox.argvs("sg"), [], "sg can't help, so it isn't tried")
        self.assertEqual(self.launched(), [])

    def test_in_the_kvm_group_but_not_this_session_reruns_through_sg_once(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["someone", "tester"])
        result = self.start("tv_api25", "-no-window")
        # The fake sg can't grant the group, so the re-run still has no access and must stop
        # instead of re-running itself again.
        self.assertFailed(result, "No access to")
        [argv] = self.sandbox.argvs("sg")
        self.assertEqual(argv[:2], ["kvm", "-c"])
        self.assertIn("_IN_SG_KVM=1", argv[2])
        self.assertIn("tv_api25 -no-window", argv[2], "the re-run keeps the AVD and flags")

    def test_the_sg_rerun_keeps_the_scripts_own_flags(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        self.start("--wait-for-home", "--quick")
        [argv] = self.sandbox.argvs("sg")
        self.assertIn("tv_api25 --quick --wait-for-home", argv[2])

    def test_the_sg_rerun_finds_the_script_when_called_through_a_symlink(self):
        self.sandbox.kvm.chmod(0o444)
        self.sandbox.set_behavior(kvm_group_members=["tester"])
        self.sandbox.run("start-emulator.sh", how="symlink")
        [argv] = self.sandbox.argvs("sg")
        self.assertIn(f"{self.sandbox.tools}/bin/start-emulator.sh tv_api25", argv[2],
                      "sg's shell may not have the same $PATH, so the real path is used")


class WslToolbar(EmulatorTestCase):
    def setUp(self):
        super().setUp()
        self.sandbox.add_avd("tv_api25")

    def toolbar_calls(self):
        return [argv for argv in self.sandbox.argvs("python3") if argv[0].endswith("wslg-toolbar.py")]

    def test_hidden_after_boot_on_wsl(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start())
        self.assertEqual([argv[1:] for argv in self.toolbar_calls()], [["tv_api25", "hide"]])

    def test_toolbar_script_found_when_called_through_a_symlink(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.sandbox.run("start-emulator.sh", how="symlink"))
        [argv] = self.toolbar_calls()
        self.assertEqual(argv[0], str(self.sandbox.tools / "bin" / "wslg-toolbar.py"))

    def test_emulator_toolbar_show(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start(env={"EMULATOR_TOOLBAR": "show"}))
        self.assertEqual([argv[1:] for argv in self.toolbar_calls()], [["tv_api25", "show"]])

    def test_untouched_outside_wsl(self):
        self.assertSucceeded(self.start())
        self.assertEqual(self.toolbar_calls(), [])

    def test_untouched_without_a_window(self):
        self.sandbox.wsl()
        self.assertSucceeded(self.start("-no-window"))
        self.assertEqual(self.toolbar_calls(), [])

    def test_a_toolbar_failure_does_not_fail_the_booted_emulator(self):
        self.sandbox.wsl()
        self.sandbox.set_behavior(python3_exit=1)
        self.assertSucceeded(self.start())


class Usage(EmulatorTestCase):
    def test_missing_emulator_package(self):
        self.sandbox.install_sdk(self.sandbox.root / "partial", tools=["adb"])
        result = self.start(env={"ANDROID_HOME": str(self.sandbox.root / "partial")})
        self.assertFailed(result, 'android sdk install --no-metrics "emulator"')
